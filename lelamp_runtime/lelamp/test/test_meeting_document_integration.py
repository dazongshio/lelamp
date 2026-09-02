from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lelamp.office_agent.document_workspace import DocumentWorkspace
from lelamp.office_agent.web_console import RequestContext, WebConsoleServer


class MeetingDocumentIntegrationTest(unittest.TestCase):
    def test_repeated_finalization_reuses_one_file_and_document_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "generated-minutes.md"
            source.write_text(
                "# 临时标题\n\n## 会议摘要\n\n讨论产品发布。\n\n"
                "## 关键决定\n\n按计划发布。\n\n## 行动项\n\n- 完成验收\n\n"
                "## 完整转写\n\n这是转写内容。\n",
                encoding="utf-8",
            )
            server = object.__new__(WebConsoleServer)
            server.runtime = SimpleNamespace(config=SimpleNamespace(workspace_dir=workspace))
            server.documents_workspace = DocumentWorkspace(workspace)
            server.record_audit = lambda *args, **kwargs: {}
            context = RequestContext("request", "lelamp-web", "127.0.0.1")
            followup = {"minutes": {"path": str(source)}}

            first = server.materialize_meeting_final_markdown(
                title="产品发布讨论",
                meeting_id="tingwu_20260728_001",
                started_at="2026-07-28T10:00:00+08:00",
                followup=followup,
                minutes_result={},
                ctx=context,
            )
            second = server.materialize_meeting_final_markdown(
                title="产品发布决策复盘",
                meeting_id="tingwu_20260728_001",
                started_at="2026-07-28T10:00:00+08:00",
                followup=followup,
                minutes_result={},
                ctx=context,
            )

            self.assertEqual(first["document_id"], second["document_id"])
            self.assertEqual(first["path"], second["path"])
            self.assertEqual(len(list((workspace / "meetings" / "会议记录").glob("*.md"))), 1)
            document = server.documents_workspace.get_document(
                str(second["document_id"]),
                actor=server.document_actor(context),
            )
            self.assertEqual(document["title"], "产品发布决策复盘")
            self.assertIn("## 会议摘要", str(document["content"]))
            self.assertIn("## 关键决定", str(document["content"]))
            self.assertIn("## 行动项", str(document["content"]))
            self.assertIn("## 完整转写", str(document["content"]))
            self.assertTrue(str(second["document_url"]).startswith("/documents?document="))


if __name__ == "__main__":
    unittest.main()
