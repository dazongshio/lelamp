from __future__ import annotations

import base64
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import lelamp.office_agent.document_workspace as document_workspace_module
from lelamp.office_agent.document_workspace import (
    DocumentActor,
    DocumentWorkspace,
    DocumentWorkspaceError,
)


class DocumentWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = DocumentWorkspace(self.root)
        self.owner = DocumentActor("owner", "所有者")
        self.editor = DocumentActor("editor", "编辑者")
        self.commenter = DocumentActor("commenter", "评论者")
        self.viewer = DocumentActor("viewer", "查看者")
        self.stranger = DocumentActor("stranger", "未授权用户", role="owner")
        self.document = self.store.create_document(
            actor=self.owner,
            title="权限测试",
            content="# 权限测试\n\n原始内容\n",
        )
        self.document_id = str(self.document["id"])
        self.store.set_permissions(
            self.document_id,
            actor=self.owner,
            permissions=[
                {"principal_id": "editor", "display_name": "编辑者", "role": "editor"},
                {"principal_id": "commenter", "display_name": "评论者", "role": "commenter"},
                {"principal_id": "viewer", "display_name": "查看者", "role": "viewer"},
            ],
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assert_code(self, code: str, callback) -> None:
        with self.assertRaises(DocumentWorkspaceError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_permission_matrix_and_forged_role(self) -> None:
        self.assertTrue(self.store.get_document(self.document_id, actor=self.viewer))
        self.store.update_document(self.document_id, actor=self.editor, content="编辑者内容")
        self.store.add_comment(self.document_id, actor=self.commenter, body="评论")
        self.assert_code(
            "document_permission_denied",
            lambda: self.store.update_document(self.document_id, actor=self.viewer, content="越权"),
        )
        self.assert_code(
            "document_permission_denied",
            lambda: self.store.update_document(self.document_id, actor=self.commenter, content="越权"),
        )
        self.assert_code(
            "permission_update_forbidden",
            lambda: self.store.set_permissions(self.document_id, actor=self.editor, permissions=[]),
        )
        self.assert_code(
            "document_permission_denied",
            lambda: self.store.get_document(self.document_id, actor=self.stranger),
        )

    def test_comment_reply_resolve_and_reopen(self) -> None:
        parent = self.store.add_comment(
            self.document_id,
            actor=self.commenter,
            body="请确认",
            anchor_text="原始内容",
        )
        reply = self.store.add_comment(
            self.document_id,
            actor=self.editor,
            body="已确认",
            parent_id=str(parent["id"]),
        )
        self.assertEqual(reply["parent_id"], parent["id"])
        resolved = self.store.update_comment(
            self.document_id,
            str(parent["id"]),
            actor=self.commenter,
            resolved=True,
        )
        self.assertTrue(resolved["resolved"])
        reopened = self.store.update_comment(
            self.document_id,
            str(parent["id"]),
            actor=self.commenter,
            resolved=False,
        )
        self.assertFalse(reopened["resolved"])

    def test_version_conflict_and_restore_creates_revision(self) -> None:
        updated = self.store.update_document(
            self.document_id,
            actor=self.editor,
            content="第二版",
            base_version=1,
        )
        self.assertEqual(updated["content_version"], 2)
        self.assert_code(
            "document_version_conflict",
            lambda: self.store.update_document(
                self.document_id,
                actor=self.editor,
                content="冲突内容",
                base_version=1,
            ),
        )
        history = self.store.list_revisions(self.document_id, actor=self.viewer)
        creation = history[-1]
        creation_detail = self.store.get_revision(self.document_id, str(creation["id"]), actor=self.viewer)
        self.assertIn("原始内容", str(creation_detail["content"]))
        restored = self.store.restore_revision(
            self.document_id,
            str(creation["id"]),
            actor=self.editor,
        )
        self.assertEqual(restored["content_version"], 3)
        self.assertIn("原始内容", str(restored["content"]))
        self.assertEqual(len(self.store.list_revisions(self.document_id, actor=self.viewer)), 3)

    def test_import_is_idempotent_and_preserves_source(self) -> None:
        source = self.root / "旧会议.md"
        source.write_text("# 第一次会议\n\n第一版\n", encoding="utf-8")
        first = self.store.import_markdown(
            actor=self.owner,
            source_path=source,
            source_type="meeting",
            external_id="meeting-001",
            update_existing=True,
        )
        source.write_text("# 第二次会议\n\n第二版\n", encoding="utf-8")
        second = self.store.import_markdown(
            actor=self.owner,
            source_path=source,
            source_type="meeting",
            external_id="meeting-001",
            update_existing=True,
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(source.read_text(encoding="utf-8"), "# 第二次会议\n\n第二版\n")
        self.assertEqual(second["content"], "# 第二次会议\n\n第二版\n")

    def test_attachment_filename_is_sanitized_and_hashed(self) -> None:
        raw = b"safe attachment"
        attachment = self.store.add_attachment(
            self.document_id,
            actor=self.editor,
            filename="../../外部.txt",
            content_base64=base64.b64encode(raw).decode("ascii"),
        )
        self.assertEqual(attachment["filename"], "外部.txt")
        path, loaded = self.store.attachment_path(
            self.document_id,
            str(attachment["id"]),
            actor=self.viewer,
        )
        self.assertTrue(path.is_relative_to(self.store.attachments_dir))
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(loaded["checksum"], attachment["checksum"])

    def test_attachment_limit_and_invalid_payload(self) -> None:
        self.assert_code(
            "invalid_attachment",
            lambda: self.store.add_attachment(
                self.document_id,
                actor=self.editor,
                filename="bad.bin",
                content_base64="not-base64!",
            ),
        )
        with patch.object(document_workspace_module, "MAX_ATTACHMENT_BYTES", 4):
            self.assert_code(
                "attachment_too_large",
                lambda: self.store.add_attachment(
                    self.document_id,
                    actor=self.editor,
                    filename="large.bin",
                    content_base64=base64.b64encode(b"12345").decode("ascii"),
                ),
            )

    def test_import_rejects_files_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            source = Path(outside) / "outside.md"
            source.write_text("# 外部", encoding="utf-8")
            self.assert_code(
                "source_outside_workspace",
                lambda: self.store.import_markdown(actor=self.owner, source_path=source),
            )

    def test_large_document_and_deterministic_markdown_export(self) -> None:
        content = "# 十万字测试\n\n" + ("这是用于性能验证的中文段落。" * 8000)
        self.assertGreater(len(content), 100_000)
        started = time.monotonic()
        document = self.store.create_document(actor=self.owner, title="十万字测试", content=content)
        loaded = self.store.get_document(str(document["id"]), actor=self.owner)
        filename_one, export_one = self.store.export_markdown(str(document["id"]), actor=self.owner)
        filename_two, export_two = self.store.export_markdown(str(document["id"]), actor=self.owner)
        self.assertEqual(loaded["content"], content)
        self.assertEqual((filename_one, export_one), (filename_two, export_two))
        self.assertLess(time.monotonic() - started, 2.0)

    def test_purge_requires_owner_and_trashed_status(self) -> None:
        self.assert_code(
            "document_purge_forbidden",
            lambda: self.store.purge_document(self.document_id, actor=self.editor),
        )
        self.assert_code(
            "document_not_trashed",
            lambda: self.store.purge_document(self.document_id, actor=self.owner),
        )
        self.store.trash_document(self.document_id, actor=self.owner)
        result = self.store.purge_document(self.document_id, actor=self.owner)
        self.assertEqual(result, {"id": self.document_id, "status": "purged"})
        self.assert_code(
            "document_not_found",
            lambda: self.store.get_document(self.document_id, actor=self.owner),
        )

    def test_migration_handles_empty_chinese_images_and_invalid_utf8(self) -> None:
        cases = {
            "空文档.md": b"",
            "中文文件名.md": "# 中文标题\n\n![设备图片](images/device.png)\n".encode(),
            "损坏编码.md": b"# damaged\n\n\xff\xfe",
        }
        imported = []
        for filename, raw in cases.items():
            source = self.root / filename
            source.write_bytes(raw)
            imported.append(self.store.import_markdown(actor=self.owner, source_path=source))
            self.assertEqual(source.read_bytes(), raw)
        self.assertEqual(imported[0]["title"], "空文档")
        self.assertIn("![设备图片](images/device.png)", str(imported[1]["content"]))
        self.assertIn("\ufffd", str(imported[2]["content"]))


if __name__ == "__main__":
    unittest.main()
