from __future__ import annotations

import unittest

from lelamp.office_agent.mhs_adapter import MHSCommandError
from lelamp.office_agent.mhs_mcp_server import MCPServer


class FakeAdapter:
    def describe(self):
        return {"schema": "mhs-ready/0.1"}

    def read_status(self):
        return {"hardware_enabled": False}

    def set_expression(self, state, *, confirmed=False):
        if not confirmed:
            raise MHSCommandError("confirmation required")
        return {"status": "dry_run", "state": state}

    def play_safe_motion(self, recording, *, confirmed=False):
        return {"recording": recording, "confirmed": confirmed}

    def observe_camera_once(self, **kwargs):
        return kwargs

    def emergency_stop(self):
        return {"status": "disabled"}


class MCPServerTest(unittest.TestCase):
    def setUp(self):
        self.server = MCPServer(FakeAdapter())

    def test_initialize_and_tools(self):
        initialized = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-06-18")
        listed = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertIn("lelamp_emergency_stop", names)
        self.assertIn("lelamp_set_expression", names)

    def test_physical_action_requires_confirmation(self):
        response = self.server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "lelamp_set_expression", "arguments": {"state": "thinking", "confirmed": False}}})
        self.assertEqual(response["error"]["code"], -32602)

    def test_confirmed_action_returns_structured_content(self):
        response = self.server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "lelamp_set_expression", "arguments": {"state": "thinking", "confirmed": True}}})
        self.assertEqual(response["result"]["structuredContent"]["status"], "dry_run")


if __name__ == "__main__":
    unittest.main()
