from __future__ import annotations

import json
import sys
from typing import Any, Callable

from .mhs_adapter import LeLampMHSAdapter, MHSCommandError


def _tools() -> list[dict[str, Any]]:
    confirmation = {"confirmed": {"type": "boolean", "description": "Explicit user approval for this action."}}
    return [
        {"name": "lelamp_describe", "description": "Describe LeLamp capabilities and safety constraints.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "lelamp_read_status", "description": "Run bounded, read-only hardware and sensor probes.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "lelamp_set_expression", "description": "Set an allowlisted lamp expression (motion plus RGB).", "inputSchema": {"type": "object", "properties": {"state": {"type": "string", "enum": ["idle", "wake", "listening", "thinking", "speaking", "reminder", "blocked", "success", "error", "meeting", "projecting"]}, **confirmation}, "required": ["state", "confirmed"]}},
        {"name": "lelamp_play_safe_motion", "description": "Play an allowlisted prerecorded motion; raw motor access is unavailable.", "inputSchema": {"type": "object", "properties": {"recording": {"type": "string"}, **confirmation}, "required": ["recording", "confirmed"]}},
        {"name": "lelamp_observe_camera", "description": "Capture and analyze one camera frame after explicit approval.", "inputSchema": {"type": "object", "properties": {"camera_index": {"type": "integer", "enum": [0, 1], "default": 0}, "rotation_degrees": {"type": "integer", "enum": [0, 90, 180, 270], "default": 0}, **confirmation}, "required": ["confirmed"]}},
        {"name": "lelamp_emergency_stop", "description": "Interrupt motion and disable motor torque. No confirmation required.", "inputSchema": {"type": "object", "properties": {}}},
    ]


class MCPServer:
    def __init__(self, adapter: LeLampMHSAdapter | None = None):
        self.adapter = adapter or LeLampMHSAdapter()

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        if request_id is None:
            return None
        try:
            if method == "initialize":
                version = request.get("params", {}).get("protocolVersion", "2025-06-18")
                result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "lelamp-mhs-ready", "version": "0.1.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": _tools()}
            elif method == "tools/call":
                result = self._call_tool(request.get("params", {}))
            else:
                return self._error(request_id, -32601, f"Method not found: {method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (MHSCommandError, TypeError, ValueError) as exc:
            return self._error(request_id, -32602, str(exc))
        except Exception as exc:
            return self._error(request_id, -32603, f"Device operation failed: {exc}")

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name", ""))
        args = params.get("arguments") or {}
        calls: dict[str, Callable[[], dict[str, Any]]] = {
            "lelamp_describe": self.adapter.describe,
            "lelamp_read_status": self.adapter.read_status,
            "lelamp_set_expression": lambda: self.adapter.set_expression(str(args.get("state", "")), confirmed=args.get("confirmed") is True),
            "lelamp_play_safe_motion": lambda: self.adapter.play_safe_motion(str(args.get("recording", "")), confirmed=args.get("confirmed") is True),
            "lelamp_observe_camera": lambda: self.adapter.observe_camera_once(camera_index=int(args.get("camera_index", 0)), rotation_degrees=int(args.get("rotation_degrees", 0)), confirmed=args.get("confirmed") is True),
            "lelamp_emergency_stop": self.adapter.emergency_stop,
        }
        if name not in calls:
            raise MHSCommandError(f"Unknown tool: {name}")
        payload = calls[name]()
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "structuredContent": payload, "isError": False}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def main() -> None:
    server = MCPServer()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = server.handle(request)
        except Exception as exc:
            response = MCPServer._error(None, -32700, f"Parse error: {exc}")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
