from __future__ import annotations

import time

from ..desktop_automation import parse_browser_step
from ..desktop_companion import DesktopCompanionService


def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)


TEST_PNG_BYTES = bytes.fromhex(
    "89504E470D0A1A0A0000000D4948445200000001000000010804000000B51C0C02"
    "0000000B4944415478DA63FCFF1F0003030200EFBFA7DB0000000049454E44AE426082"
)


class ConsoleTestRuntimeMixin:
    def run_console_test(self, test_id: str) -> dict[str, object]:
        started_at = time.monotonic()
        try:
            result = self._run_console_test(test_id)
        except Exception as exc:  # The test center must show backend failures instead of breaking the UI.
            payload = {
                "test_id": test_id,
                "status": "error",
                "duration_ms": int((time.monotonic() - started_at) * 1000),
                "error": str(exc)[:1000],
            }
            self.audit.record("web_console.test", status="error", target=test_id, details=payload)
            return payload

        result_status = str(result.get("status") or "ok")
        payload = {
            "test_id": test_id,
            "status": result_status,
            "duration_ms": int((time.monotonic() - started_at) * 1000),
            "result": result,
        }
        audit_status = "error" if result_status == "error" else "ok"
        self.audit.record(
            "web_console.test",
            status=audit_status,
            target=test_id,
            details={"result_status": result_status, "duration_ms": payload["duration_ms"]},
        )
        return payload

    def _run_console_test(self, test_id: str) -> dict[str, object]:
        if test_id == "security":
            return self.api_security()
        if test_id == "skills":
            return {"status": "ok", "skills": self.runtime.skills.list_skills()}
        if test_id == "readiness":
            return self.runtime.readiness_report()
        if test_id == "p0_status":
            return {"status": "ok", **self.runtime.p0.status()}
        if test_id == "shared_note":
            item = self.shared_space.put_note(
                "console_test_shared_note",
                "OpenClaw 前端测试笔记\n决定: 使用 shared_inbox 作为办公电脑公共空间\n待办: 检查审计日志",
                source="web_console_test",
            )
            return {"status": "ok", "file": item.as_dict()}
        if test_id == "workspace_blocked_read":
            try:
                self.runtime.workspace.read_text("../outside-openclaw-test.txt")
            except ValueError as exc:
                return {"status": "ok", "expected_blocked": True, "blocked_reason": str(exc)}
            return {"status": "error", "expected_blocked": False, "reason": "Unauthorized read unexpectedly succeeded."}
        if test_id == "document_analysis":
            item = self.shared_space.put_note(
                "console_test_contract",
                "# 测试合同\n付款: 30天内\n保密: 双方不得泄露资料\n终止: 任一方违约可终止\nOpenClawTestSearchMarker",
                source="web_console_test",
            )
            analysis = self.runtime.documents.analyze_text_file(item.workspace_name)
            summary = self.runtime.documents.summarize_text_file(item.workspace_name, "outline")
            return {"status": "ok", "file": item.as_dict(), "analysis": analysis, "summary": summary}
        if test_id == "meeting_followup":
            item = self.shared_space.put_note(
                "console_test_meeting_transcript",
                "Alice: 决定: 先用显示器测试投影内容\nBob: 待办: 检查 sandbox 和 audit log\nAlice: 确认: 不自动发送邮件",
                source="web_console_test",
            )
            self.runtime.meeting.parse_transcript_file(item.workspace_name, "前端测试会议", ["Alice", "Bob"])
            package = self.runtime.p0.generate_meeting_followup_package(
                recipient="待填写收件人",
                create_reminders=True,
                render_projection=True,
            )
            return {"status": "ok", "transcript_file": item.as_dict(), "package": package}
        if test_id == "projection_status":
            card = self.runtime.projection.render_status_card(
                "前端测试状态卡",
                "ready",
                details=["sandbox 默认开启", "projection_out 已写入"],
                accent="blue",
            )
            return {"status": "ok", "card": card}
        if test_id == "projection_countdown":
            card = self.runtime.projection.render_countdown("前端测试倒计时", 90, message="显示器预览测试。")
            return {"status": "ok", "card": card}
        if test_id == "projection_action":
            card = self.runtime.projection.render_action_card(
                "前端测试行动卡",
                ["用户确认后再执行桌面动作", "导出会议跟进包"],
                decisions=["继续使用显示器进行投影验证"],
            )
            return {"status": "ok", "card": card}
        if test_id == "projection_calibration":
            plan = self.runtime.projection.create_projection_calibration_plan("display_preview", "office_lighting")
            return {"status": "adapter_ready", "plan": plan}
        if test_id == "scan_register":
            item = self.shared_space.put_bytes("console_test_scan.png", TEST_PNG_BYTES, source="web_console_test")
            registered = self.runtime.scanning.register_scan_image(item.workspace_name, "document")
            return {"status": "ok", "file": item.as_dict(), "registered": registered}
        if test_id == "scan_ocr":
            item = self.shared_space.put_bytes("console_test_ocr.png", TEST_PNG_BYTES, source="web_console_test")
            result = self.runtime.scanning.run_ocr(item.workspace_name, "chi_sim+eng")
            return result
        if test_id == "ocr_text_summary":
            item = self.shared_space.put_note(
                "console_test_ocr_text",
                "Ada Lovelace\nOpenClaw Office\nada@example.com\n13800138000\n保密: NDA required",
                source="web_console_test",
            )
            summary = self.runtime.scanning.summarize_ocr_text(item.workspace_name)
            card = self.runtime.scanning.analyze_business_card_text(item.workspace_name)
            return {"status": "ok", "file": item.as_dict(), "summary": summary, "business_card": card}
        if test_id == "screen_capture":
            return self.runtime.screen.capture_screen()
        if test_id == "screen_summary":
            return self.runtime.screen.summarize_current_screen()
        if test_id == "desktop_audit_only":
            if self.runtime.config.desktop_backend != "audit_only":
                return {
                    "status": "needs_confirmation",
                    "reason": "desktop backend is not audit_only; the test center will not launch desktop actions automatically.",
                    "desktop_backend": self.runtime.config.desktop_backend,
                }
            return self.runtime.desktop.open_url("https://example.com/openclaw-test")
        if test_id == "desktop_full_control_gate":
            permission = self.runtime.desktop.request_operation("前端测试：尝试全权桌面操作门禁")
            return {"status": "ok" if not permission.get("allowed") else "needs_confirmation", "permission": permission}
        if test_id == "desktop_task_queue":
            task = self.runtime.desktop_tasks.request_task(
                "前端测试：办公电脑查看 shared_inbox",
                ["打开共享空间", "查看测试文件", "等待用户确认后再执行"],
                source="web_console_test",
            )
            return {"status": "ok", "task": task}
        if test_id == "browser_automation_status":
            task = self.runtime.desktop_tasks.request_task(
                "前端测试：受控浏览器打开 example.com",
                ["open https://example.com", "extract text", "screenshot"],
                source="web_console_test",
                requires_full_control=False,
            )
            self.runtime.desktop_tasks.update_status(str(task["id"]), "approved", actor="web_console_test")
            status = self.runtime.browser_automation.status(check_launch=False)
            step_parse = [parse_browser_step(step["description"]).audit_dict() for step in task["steps"]]
            return {"status": status["status"], "backend": status, "task": task, "parsed_steps": step_parse}
        if test_id == "desktop_companion":
            companion = DesktopCompanionService(
                workspace=self.runtime.workspace,
                audit=self.runtime.audit,
                backend="audit_only",
                permission_mode=self.runtime.config.permission_mode,
            )
            return {
                "status": "ok",
                "companion": companion.status(),
                "approved_tasks": companion.list_approved_tasks(limit=5),
            }
        if test_id == "lelamp_state":
            return {"status": "ok", "cue": self.runtime.lelamp_experience.state_cue("thinking")}
        if test_id == "environment_event":
            result = self.runtime.environment.ingest(
                {
                    "presence": True,
                    "people_count": 2,
                    "lux": 48,
                    "speech_active": True,
                    "projector_blocked": False,
                    "calendar_event_now": True,
                }
            )
            return {"status": "ok", **result}
        if test_id == "camera_observe":
            return self.runtime.camera_observer.observe_once()
        if test_id == "hardware_status":
            return {"status": "ok", **self.api_hardware_status()}
        if test_id == "smart_home_status":
            return {"status": "ok", "smart_home": self.runtime.smart_home.status()}
        if test_id == "smart_home_control_guard":
            status = self.runtime.smart_home.status()
            if status.get("home_assistant_configured") or status.get("webhook_configured"):
                return {
                    "status": "needs_confirmation",
                    "reason": "Smart-home bridge is configured; the test center reports configuration instead of toggling a real device.",
                    "smart_home": status,
                }
            return self.runtime.smart_home.control("打开办公室测试灯")
        if test_id == "xiaoai_utility":
            return {"status": "ok", "answer": self.runtime.xiaoai.answer_utility("计算 36*18")}
        if test_id == "xiaoai_features":
            return {"status": "ok", "features": self.runtime.xiaoai.feature_matrix()}
        if test_id == "intent_router":
            return {"status": "ok", "route": self.runtime.intent_router.route("帮我生成会议 follow-up 并投影确认页").as_dict()}
        if test_id == "local_file_search":
            item = self.shared_space.put_note(
                "console_test_search_source",
                "OpenClawTestSearchMarker\n这是用于前端测试中心的本地文件搜索样本。",
                source="web_console_test",
            )
            result = self.runtime.file_search.search("OpenClawTestSearchMarker", limit=5)
            return {"status": "ok", "source": item.as_dict(), "search": result}
        if test_id == "daily_reminder":
            reminder = self.runtime.daily.create_reminder("10分钟后提醒我检查前端测试结果")
            agenda = self.runtime.daily.agenda("today")
            return {"status": "ok", "reminder": reminder, "agenda": agenda}
        if test_id == "mobile_bridge":
            result = self.runtime.mobile_bridge.request("找手机")
            return {"status": result.get("status"), "mobile_bridge": self.runtime.mobile_bridge.status(), "request": result}
        if test_id == "voice_stack_status":
            return self.build_voice_status()
        if test_id == "audit_recent":
            return {"status": "ok", **self.api_recent_audit(limit=20)}
        raise ValueError(f"Unknown console test: {test_id}")

