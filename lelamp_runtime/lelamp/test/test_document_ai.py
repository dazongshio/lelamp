from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lelamp.office_agent.document_workspace import DocumentActor, DocumentWorkspace
from lelamp.office_agent.web_console import RequestContext, WebConsoleServer


class DocumentAiTest(unittest.TestCase):
    def test_ai_preview_does_not_write_until_explicit_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            server = object.__new__(WebConsoleServer)
            server.runtime = SimpleNamespace(config=SimpleNamespace(workspace_dir=Path(temporary)))
            server.documents_workspace = DocumentWorkspace(Path(temporary))
            events: list[str] = []
            server.record_audit = lambda action, *args, **kwargs: events.append(action) or {}
            server.generate_document_ai_suggestion = lambda operation, text: f"建议：{text}"
            actor = DocumentActor("lelamp-web", "本机用户")
            context = RequestContext("request", "lelamp-web", "127.0.0.1")
            replay_one = server.api_docs_create(
                {"title": "幂等文档", "idempotency_key": "request-001"},
                context,
            )
            replay_two = server.api_docs_create(
                {"title": "不会重复创建", "idempotency_key": "request-001"},
                context,
            )
            self.assertEqual(replay_one["document"]["id"], replay_two["document"]["id"])
            self.assertTrue(replay_two["idempotent_replay"])
            document = server.documents_workspace.create_document(
                actor=actor,
                title="AI 测试",
                content="# 原文\n\n需要优化的内容。\n",
            )
            document_id = str(document["id"])
            preview = server.api_docs_post_route(
                f"/api/docs/{document_id}/ai",
                {"operation": "rewrite", "selected_text": "需要优化的内容"},
                context,
            )
            unchanged = server.documents_workspace.get_document(document_id, actor=actor)
            self.assertEqual(unchanged["content_version"], 1)
            self.assertEqual(unchanged["content"], "# 原文\n\n需要优化的内容。\n")
            self.assertEqual(preview["suggestion"], "建议：需要优化的内容")

            applied = server.api_docs_post_route(
                f"/api/docs/{document_id}/ai/apply",
                {
                    "operation": "rewrite",
                    "content": "# 原文\n\n建议后的内容。\n",
                    "base_version": preview["base_version"],
                    "mode": "replace",
                },
                context,
            )
            self.assertEqual(applied["document"]["content_version"], 2)
            self.assertEqual(applied["document"]["content"], "# 原文\n\n建议后的内容。\n")
            self.assertIn("documents.ai.read", events)
            self.assertIn("documents.ai.write", events)
            history = server.documents_workspace.list_revisions(document_id, actor=actor)
            self.assertTrue(any(str(item["summary"]).startswith("AI 建议") for item in history))


if __name__ == "__main__":
    unittest.main()
