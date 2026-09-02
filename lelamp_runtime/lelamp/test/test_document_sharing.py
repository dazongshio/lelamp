from __future__ import annotations

import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

from lelamp.office_agent.document_workspace import DocumentActor, DocumentWorkspace, DocumentWorkspaceError
from lelamp.office_agent.web_console import RequestContext, WebConsoleServer


class DocumentSharingTest(unittest.TestCase):
    def test_scan_is_served_as_a_frontend_route(self) -> None:
        server = object.__new__(WebConsoleServer)
        self.assertTrue(server._is_frontend_route("/scan"))

    def test_scoped_share_session_obeys_document_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            server = object.__new__(WebConsoleServer)
            server.token = "test-console-secret"
            server.runtime = SimpleNamespace(config=SimpleNamespace(workspace_dir=Path(temporary)))
            server.documents_workspace = DocumentWorkspace(Path(temporary))
            server.record_audit = lambda *args, **kwargs: {}
            owner = DocumentActor("lelamp-web", "本机用户")
            document = server.documents_workspace.create_document(
                actor=owner,
                title="分享测试",
                content="# 只读内容\n",
            )
            document_id = str(document["id"])
            server.documents_workspace.set_permissions(
                document_id,
                actor=owner,
                permissions=[
                    {
                        "principal_type": "user",
                        "principal_id": "guest-viewer",
                        "display_name": "访客",
                        "role": "viewer",
                    }
                ],
            )
            owner_context = RequestContext("request", "lelamp-web", "127.0.0.1")
            shared = server.api_docs_post_route(
                f"/api/docs/{document_id}/share-token",
                {"principal_id": "guest-viewer"},
                owner_context,
            )
            query = urllib.parse.parse_qs(urllib.parse.urlparse(str(shared["share_url"])).query)
            session_token = query["document_session"][0]
            headers = {"X-LeLamp-Document-Session": session_token}

            self.assertTrue(
                server._authorized(
                    urllib.parse.urlparse(f"/api/docs/{document_id}"),
                    headers,
                    "GET",
                )
            )
            self.assertFalse(
                server._authorized(
                    urllib.parse.urlparse("/api/docs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
                    headers,
                    "GET",
                )
            )
            self.assertFalse(server._authorized(urllib.parse.urlparse("/api/docs"), headers, "POST"))
            payload = server._document_session_payload(session_token)
            self.assertEqual(payload["actor_id"], "guest-viewer")
            viewer = DocumentActor(str(payload["actor_id"]), str(payload["display_name"]))
            self.assertEqual(server.documents_workspace.get_document(document_id, actor=viewer)["role"], "viewer")
            with self.assertRaises(DocumentWorkspaceError):
                server.documents_workspace.update_document(document_id, actor=viewer, content="越权")
            forged = f"{session_token[:-1]}{'a' if not session_token.endswith('a') else 'b'}"
            self.assertIsNone(server._document_session_payload(forged))
            origin_context = RequestContext(
                "request",
                "lelamp-web",
                "127.0.0.1",
                host="device.local:8790",
            )
            self.assertTrue(server._trusted_write_origin({"Origin": "http://device.local:8790"}, origin_context))
            self.assertFalse(server._trusted_write_origin({"Origin": "https://attacker.example"}, origin_context))
            self.assertFalse(server._trusted_write_origin({"Origin": "null"}, origin_context))
            shared_html = server._render_react_console(include_token=False)
            self.assertNotIn(server.token, shared_html or "")

    def test_existing_meeting_and_scan_results_sync_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            meeting = workspace / "meetings" / "会议记录" / "2026-07-29_产品评审.md"
            meeting.parent.mkdir(parents=True)
            meeting.write_text("# 产品评审\n\n## 会议摘要\n\n确认发布计划。\n", encoding="utf-8")
            scan = workspace / "scans" / "2026" / "07" / "29" / "paper_ocr.txt"
            scan.parent.mkdir(parents=True)
            scan.write_text("扫描识别正文", encoding="utf-8")

            server = object.__new__(WebConsoleServer)
            server.runtime = SimpleNamespace(config=SimpleNamespace(workspace_dir=workspace))
            server.documents_workspace = DocumentWorkspace(workspace)
            server._document_result_sync_lock = threading.Lock()
            server._document_result_sync_completed = False
            server.record_audit = lambda *args, **kwargs: {}
            context = RequestContext("request", "lelamp-web", "127.0.0.1")

            first = server.sync_existing_result_documents(context)
            second = server.sync_existing_result_documents(context)
            actor = DocumentActor("lelamp-web", "本机用户")
            meetings = server.documents_workspace.list_documents(actor=actor, source_type="meeting")
            scans = server.documents_workspace.list_documents(actor=actor, source_type="scan")

            self.assertEqual(first["imported_count"], 2)
            self.assertTrue(second["cached"])
            self.assertEqual(len(meetings), 1)
            self.assertEqual(len(scans), 1)
            self.assertIn("扫描识别正文", str(server.documents_workspace.get_document(str(scans[0]["id"]), actor=actor)["content"]))


if __name__ == "__main__":
    unittest.main()
