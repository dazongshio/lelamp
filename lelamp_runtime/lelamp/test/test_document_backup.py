from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from lelamp.office_agent.document_workspace import DocumentActor, DocumentWorkspace


class DocumentBackupTest(unittest.TestCase):
    def test_backup_restores_content_permissions_comments_history_and_attachments(self) -> None:
        repository = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_workspace = root / "source"
            restored_workspace = root / "restored"
            backup_dir = root / "backups"
            store = DocumentWorkspace(source_workspace)
            owner = DocumentActor("owner", "所有者")
            document = store.create_document(actor=owner, title="备份验证", content="# 正文\n")
            document_id = str(document["id"])
            store.set_permissions(
                document_id,
                actor=owner,
                permissions=[{"principal_id": "viewer", "display_name": "查看者", "role": "viewer"}],
            )
            store.add_comment(document_id, actor=owner, body="备份评论")
            store.update_document(document_id, actor=owner, content="# 第二版\n")
            store.add_attachment(
                document_id,
                actor=owner,
                filename="附件.txt",
                content_base64=base64.b64encode(b"attachment").decode(),
            )
            env = {**os.environ, "OPENCLAW_WORKSPACE": str(source_workspace)}
            archive = subprocess.check_output(
                [str(repository / "scripts" / "backup_document_workspace.sh"), str(backup_dir)],
                cwd=repository,
                env=env,
                text=True,
            ).strip()
            subprocess.run(
                [
                    str(repository / "scripts" / "restore_document_workspace.sh"),
                    archive,
                    str(restored_workspace),
                ],
                cwd=repository,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            restored = DocumentWorkspace(restored_workspace)
            loaded = restored.get_document(document_id, actor=owner)
            self.assertEqual(loaded["content"], "# 第二版\n")
            self.assertEqual(len(restored.get_permissions(document_id, actor=owner)), 2)
            self.assertEqual(len(restored.list_comments(document_id, actor=owner, include_resolved=True)), 1)
            self.assertEqual(len(restored.list_revisions(document_id, actor=owner)), 2)
            self.assertEqual(len(restored.list_attachments(document_id, actor=owner)), 1)


if __name__ == "__main__":
    unittest.main()
