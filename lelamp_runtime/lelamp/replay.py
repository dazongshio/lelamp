import argparse
import csv
import time
import os

from .follower import LeLampFollowerConfig, LeLampFollower
from lerobot.utils.robot_utils import busy_wait

def main():
    parser = argparse.ArgumentParser(description="Replay recorded actions from CSV file")
    parser.add_argument('--name', type=str, required=True, help='Name of the recording to replay')
    parser.add_argument('--port', type=str, required=True, help='Serial port for the robot')
    parser.add_argument('--id', type=str, required=True, help='ID of the robot')
    parser.add_argument('--fps', type=int, default=30, help='Frames per second for replay (default: 30)')
    parser.add_argument(
        '--keep-torque-on-disconnect',
        action='store_true',
        help='Close the serial port without disabling servo torque. Useful when a servo reports overload during disconnect.',
    )
    args = parser.parse_args()

    robot_config = LeLampFollowerConfig(
        port=args.port,
        id=args.id,
        disable_torque_on_disconnect=not args.keep_torque_on_disconnect,
    )
    robot = LeLampFollower(robot_config)
    robot.connect(calibrate=False)

    # Build CSV filename from name and lamp ID
    recordings_dir = os.path.join(os.path.dirname(__file__), "recordings")
    csv_filename = f"{args.name}.csv"
    csv_path = os.path.join(recordings_dir, csv_filename)

    # Read CSV file and replay actions
    with open(csv_path, 'r') as csvfile:
        csv_reader = csv.DictReader(csvfile)
        actions = list(csv_reader)
    
    print(f"Replaying {len(actions)} actions from {csv_path}")
    
    try:
        for row in actions:
            t0 = time.perf_counter()

            # Extract action data (exclude timestamp column)
            action = {key: float(value) for key, value in row.items() if key != 'timestamp'}
            robot.send_action(action)

            busy_wait(1.0 / args.fps - (time.perf_counter() - t0))
    finally:
        if robot.is_connected:
            try:
                robot.disconnect()
            except RuntimeError as exc:
                print(f"Warning: failed to disable torque during disconnect: {exc}")
                print("Closing serial port without disabling torque. Power-cycle the servo bus if any motor is still holding force.")
                robot.bus.disconnect(disable_torque=False)

if __name__ == "__main__":
    main()
