import os
import csv
import time
import threading
from typing import Any, List, Dict, Optional, Tuple
from lelamp.follower import LeLampFollowerConfig, LeLampFollower
from lelamp.motor_control import LELAMP_MOTOR_ORDER
from lelamp.motion_config import get_action_keyframes, get_action_mode, load_motion_config


class AnimationService:
    def __init__(
        self,
        port: str,
        lamp_id: str,
        fps: int = 30,
        duration: float = 5.0,
        idle_recording: str = "idle",
        auto_start_idle: bool = False,
    ):
        self.port = port
        self.lamp_id = lamp_id
        self.fps = fps
        self.duration = duration
        self.idle_recording = idle_recording
        self.auto_start_idle = auto_start_idle
        self.robot_config = LeLampFollowerConfig(
            port=port,
            id=lamp_id,
            disable_torque_on_disconnect=False,
        )
        self.robot: LeLampFollower = None
        self.recordings_dir = os.path.join(os.path.dirname(__file__), "..", "..", "recordings")

        # State management
        self._recording_cache: Dict[str, List[Dict[str, float]]] = {}
        self._current_state: Optional[Dict[str, float]] = None
        self._current_recording: Optional[str] = None
        self._current_recording_started_at: float = 0.0
        self._current_play_generation: int = 0
        self._play_generation: int = 0
        self._current_frame_index: int = 0
        self._current_actions: List[Dict[str, float]] = []
        self._current_active_joint_keys: set[str] = set()
        self._interpolation_frames: int = 0
        self._interpolation_total_frames: int = 0
        self._interpolation_target: Optional[Dict[str, float]] = None

        # Custom event handling
        self._running = threading.Event()
        self._event_queue = []
        self._event_lock = threading.Lock()
        self._event_thread: Optional[threading.Thread] = None

    def start(self):
        self.robot = LeLampFollower(self.robot_config)
        self.robot.connect(calibrate=False)
        print(f"Animation service connected to {self.port}")

        # Start event processing thread
        self._running.set()
        self._event_thread = threading.Thread(target=self._event_loop, daemon=True)
        self._event_thread.start()

        if self.auto_start_idle:
            self.dispatch("play", self.idle_recording)

    def stop(self, timeout: float = 5.0):
        # Stop event processing
        self._running.clear()
        if self._event_thread and self._event_thread.is_alive():
            self._event_thread.join(timeout=timeout)

        if self.robot:
            self.robot.disconnect()
            self.robot = None

    def dispatch(self, event_type: str, payload: Any):
        """Dispatch an event - same interface as ServiceBase"""
        if not self._running.is_set():
            print(f"Animation service is not running, ignoring event {event_type}")
            return

        with self._event_lock:
            if event_type == "play":
                self._play_generation += 1
                payload = (payload, self._play_generation)
                self._event_queue = [
                    (queued_type, queued_payload)
                    for queued_type, queued_payload in self._event_queue
                    if queued_type != "play"
                ]
            self._event_queue.append((event_type, payload))

    def interrupt(self):
        """Clear queued playback and stop the current recording at the next frame."""
        with self._event_lock:
            self._play_generation += 1
            self._event_queue.clear()
            self._current_recording = None
            self._current_recording_started_at = 0.0
            self._current_play_generation = 0
            self._current_actions = []
            self._current_active_joint_keys = set()
            self._current_frame_index = 0
            self._interpolation_frames = 0
            self._interpolation_total_frames = 0
            self._interpolation_target = None

    def _event_loop(self):
        """Custom event loop that supports interruption"""
        while self._running.is_set():
            # Check for events
            with self._event_lock:
                if self._event_queue:
                    event_type, payload = self._event_queue.pop(0)
                else:
                    event_type, payload = None, None

            if event_type:
                try:
                    self.handle_event(event_type, payload)
                except Exception as e:
                    print(f"Error handling event {event_type}: {e}")

            # Continue current playback
            self._continue_playback()

            time.sleep(1.0 / self.fps)  # Frame rate timing

    def handle_event(self, event_type: str, payload: Any):
        if event_type == "play":
            if isinstance(payload, tuple) and len(payload) == 2:
                recording_name, generation = payload
            else:
                recording_name, generation = payload, self._play_generation
            self._handle_play(str(recording_name), int(generation))
        else:
            print(f"Unknown event type: {event_type}")

    def _handle_play(self, recording_name: str, generation: int):
        """Start playing a recording with interpolation from current state"""
        if not self.robot:
            print("Robot not connected")
            return
        if not self._is_play_generation_current(generation):
            print(f"Ignoring stale recording {recording_name}")
            return

        # Load the recording
        actions = self._load_recording(recording_name)
        if actions is None:
            return
        actions = [dict(action) for action in actions]
        active_joint_keys = self._action_joint_keys(actions)
        action_mode = self._action_mode(recording_name)
        if action_mode == "relative":
            actions = self._resolve_relative_actions(recording_name, actions, active_joint_keys)
        elif action_mode == "mixed":
            actions = self._hold_static_joints_for_partial_action(recording_name, actions, active_joint_keys)
        else:
            actions = self._hold_static_joints_for_partial_action(recording_name, actions, active_joint_keys)
        if not self._is_play_generation_current(generation):
            print(f"Ignoring stale recording {recording_name}")
            return

        print(f"Starting {recording_name} with interpolation")

        # Set up new playback
        self._current_recording = recording_name
        self._current_recording_started_at = time.monotonic()
        self._current_play_generation = generation
        self._current_actions = actions
        self._current_active_joint_keys = active_joint_keys
        self._current_frame_index = 0

        # If we have a current state, set up interpolation to the first frame
        if self._current_state is not None:
            self._interpolation_frames = int(self._play_interpolation_seconds(recording_name, action_mode) * self.fps)
            self._interpolation_total_frames = max(1, self._interpolation_frames)
            self._interpolation_target = actions[0]
        else:
            self._interpolation_frames = 0
            self._interpolation_total_frames = 0
            self._interpolation_target = None

    def _continue_playback(self):
        """Continue current playback - called every frame"""
        if not self._current_recording or not self._current_actions:
            return
        if self._current_play_generation != self._play_generation:
            self._clear_current_playback()
            return

        try:
            # Handle interpolation to first frame
            if self._interpolation_frames > 0 and self._interpolation_target is not None:
                # Calculate interpolation progress
                progress = 1.0 - (self._interpolation_frames / max(1, self._interpolation_total_frames))
                progress = max(0.0, min(1.0, progress))

                # Interpolate between current state and target
                interpolated_action = {}
                for joint in self._interpolation_target.keys():
                    target_val = self._interpolation_target[joint]
                    current_val = self._current_state.get(joint, target_val)
                    interpolated_action[joint] = current_val + (target_val - current_val) * progress

                self.robot.send_action(interpolated_action)
                self._current_state = {**self._current_state, **interpolated_action}
                self._interpolation_frames -= 1
                return

            # Play current frame
            if self._current_frame_index < len(self._current_actions):
                action = self._current_actions[self._current_frame_index]
                self.robot.send_action(action)
                self._current_state = {**(self._current_state or {}), **action}
                self._current_frame_index += 1
            else:
                # Recording finished
                recording_name = self._current_recording
                recording_mode = self._action_mode(recording_name)
                if recording_name != self.idle_recording and recording_mode in {"relative", "mixed"}:
                    elapsed = time.monotonic() - self._current_recording_started_at if self._current_recording_started_at else 0.0
                    print(f"Finished one-shot recording {recording_name} in {elapsed:.2f}s")
                    self._current_recording = None
                    self._current_recording_started_at = 0.0
                    self._current_play_generation = 0
                    self._current_actions = []
                    self._current_active_joint_keys = set()
                    self._current_frame_index = 0
                    self._interpolation_frames = 0
                    self._interpolation_total_frames = 0
                    self._interpolation_target = None
                elif recording_name != self.idle_recording:
                    played_joint_keys = set(self._current_active_joint_keys) or self._action_joint_keys(self._current_actions)
                    # Interpolate back to idle
                    idle_actions = self._load_recording(self.idle_recording)
                    if idle_actions is not None and len(idle_actions) > 0:
                        idle_actions = self._filter_actions(idle_actions, played_joint_keys)
                    if idle_actions is not None and len(idle_actions) > 0:
                        self._current_recording = self.idle_recording
                        self._current_recording_started_at = time.monotonic()
                        self._current_play_generation = self._play_generation
                        self._current_actions = idle_actions
                        self._current_active_joint_keys = played_joint_keys
                        self._current_frame_index = 0
                        # Set up interpolation back to idle
                        if self._current_state is not None:
                            self._interpolation_frames = int(self.duration * self.fps)
                            self._interpolation_total_frames = max(1, self._interpolation_frames)
                            self._interpolation_target = idle_actions[0]
                        else:
                            self._interpolation_frames = 0
                            self._interpolation_total_frames = 0
                            self._interpolation_target = None
                else:
                    # Loop idle recording
                    self._current_frame_index = 0

        except Exception as e:
            print(f"Error in playback: {e}")
            # Reset to safe state
            self._clear_current_playback()

    def _is_play_generation_current(self, generation: int) -> bool:
        with self._event_lock:
            return generation == self._play_generation

    def _clear_current_playback(self) -> None:
        self._current_recording = None
        self._current_recording_started_at = 0.0
        self._current_play_generation = 0
        self._current_actions = []
        self._current_active_joint_keys = set()
        self._current_frame_index = 0
        self._interpolation_frames = 0
        self._interpolation_total_frames = 0
        self._interpolation_target = None

    def _play_interpolation_seconds(self, recording_name: str, action_mode: str) -> float:
        if action_mode in {"mixed", "relative"}:
            return max(0.05, min(0.4, self.duration))
        return self.duration

    def _action_joint_keys(self, actions: List[Dict[str, float]]) -> set[str]:
        keys: set[str] = set()
        for action in actions:
            keys.update(action.keys())
        return keys

    def _filter_actions(self, actions: List[Dict[str, float]], joint_keys: set[str]) -> List[Dict[str, float]]:
        if not joint_keys:
            return actions
        return [
            {joint: value for joint, value in action.items() if joint in joint_keys}
            for action in actions
        ]

    def _hold_static_joints_for_partial_action(
        self,
        recording_name: str,
        actions: List[Dict[str, float]],
        active_joint_keys: set[str],
    ) -> List[Dict[str, float]]:
        all_joint_keys = {f"{motor}.pos" for motor in LELAMP_MOTOR_ORDER}
        static_joint_keys = all_joint_keys - active_joint_keys
        if not actions or not active_joint_keys or not static_joint_keys or recording_name == self.idle_recording:
            return actions

        current_positions = self._read_current_joint_positions()
        fallback_state = self._current_state or {}
        hold_action: Dict[str, float] = {}
        for joint in static_joint_keys:
            if joint in current_positions:
                hold_action[joint] = current_positions[joint]
            elif joint in fallback_state:
                hold_action[joint] = fallback_state[joint]
        if not hold_action:
            return actions

        print(
            f"Partial recording {recording_name}: "
            f"moving {sorted(active_joint_keys)}, holding {sorted(hold_action)}"
        )
        return [{**hold_action, **action} for action in actions]

    def _action_mode(self, recording_name: str) -> str:
        return get_action_mode(load_motion_config(), recording_name)

    def _resolve_relative_actions(
        self,
        recording_name: str,
        actions: List[Dict[str, float]],
        active_joint_keys: set[str],
    ) -> List[Dict[str, float]]:
        all_joint_keys = {f"{motor}.pos" for motor in LELAMP_MOTOR_ORDER}
        if not actions or not active_joint_keys:
            return actions

        current_positions = self._read_current_joint_positions()
        fallback_state = self._current_state or {}
        reference: Dict[str, float] = {}
        for joint in all_joint_keys:
            if joint in current_positions:
                reference[joint] = current_positions[joint]
            elif joint in fallback_state:
                reference[joint] = fallback_state[joint]
        if not all(joint in reference for joint in active_joint_keys):
            print(f"Relative recording {recording_name}: missing current positions, using raw configured values")
            return actions

        static_joint_keys = all_joint_keys - active_joint_keys
        hold_action = {joint: reference[joint] for joint in static_joint_keys if joint in reference}
        resolved: List[Dict[str, float]] = []
        for action in actions:
            resolved_action = dict(hold_action)
            for joint, delta in action.items():
                resolved_action[joint] = reference[joint] + float(delta)
            resolved.append(resolved_action)

        print(
            f"Relative recording {recording_name}: "
            f"moving {sorted(active_joint_keys)}, holding {sorted(hold_action)}"
        )
        return resolved

    def _read_current_joint_positions(self) -> Dict[str, float]:
        bus = getattr(self.robot, "bus", None)
        if bus is None:
            return {}
        try:
            positions = bus.sync_read("Present_Position")
        except Exception as e:
            print(f"Could not read current motor positions for hold: {e}")
            return {}
        result: Dict[str, float] = {}
        if not isinstance(positions, dict):
            return result
        for motor in LELAMP_MOTOR_ORDER:
            if motor not in positions:
                continue
            try:
                result[f"{motor}.pos"] = float(positions[motor])
            except (TypeError, ValueError):
                continue
        return result

    def get_available_recordings(self) -> List[str]:
        """Get list of recording names available for this lamp ID"""
        if not os.path.exists(self.recordings_dir):
            return []

        recordings = []
        suffix = f".csv"

        for filename in os.listdir(self.recordings_dir):
            if filename.endswith(suffix):
                # Remove the lamp_id suffix to get the recording name
                recording_name = filename[:-len(suffix)]
                recordings.append(recording_name)

        return sorted(recordings)

    def _load_recording(self, recording_name: str) -> Optional[List[Dict[str, float]]]:
        """Load a recording from cache or file"""
        configured_actions = get_action_keyframes(load_motion_config(), recording_name)
        if configured_actions:
            self._recording_cache[recording_name] = configured_actions
            return configured_actions

        # Check cache first
        if recording_name in self._recording_cache:
            return self._recording_cache[recording_name]

        csv_filename = f"{recording_name}.csv"
        csv_path = os.path.join(self.recordings_dir, csv_filename)

        if not os.path.exists(csv_path):
            print(f"Recording not found: {csv_path}")
            return None

        try:
            with open(csv_path, 'r') as csvfile:
                csv_reader = csv.DictReader(csvfile)
                actions = []
                for row in csv_reader:
                    # Extract action data (exclude timestamp column)
                    action = {key: float(value) for key, value in row.items() if key != 'timestamp'}
                    actions.append(action)

            # Cache the recording
            self._recording_cache[recording_name] = actions
            return actions

        except Exception as e:
            print(f"Error loading recording {recording_name}: {e}")
            return None
