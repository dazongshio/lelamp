from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..llm import LLMError
from ..projection import redact_projection_text
from ..projection_viewer import ProjectionPreviewServer, build_display_profile, latest_projection_file, markdown_to_html, save_display_profile
from ..utils import safe_filename
from ._base import ApiError, NOT_HANDLED, RequestContext, exact_payload


def _helper(name: str):
    from .. import web_console
    return getattr(web_console, name)


def list_string(*a, **kw): return _helper("list_string")(*a, **kw)
def normalize_task_status(*a, **kw): return _helper("normalize_task_status")(*a, **kw)
def optional_float(*a, **kw): return _helper("optional_float")(*a, **kw)
def probe_hardware(*a, **kw): return _helper("probe_hardware")(*a, **kw)
def require_file_path(*a, **kw): return _helper("require_file_path")(*a, **kw)
def require_string(*a, **kw): return _helper("require_string")(*a, **kw)
def safe_int(*a, **kw): return _helper("safe_int")(*a, **kw)
def status_to_audit(*a, **kw): return _helper("status_to_audit")(*a, **kw)


class ProjectionRoutesMixin:
    def api_projection_card(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = require_string(payload, "title")
        mode = str(payload.get("type") or payload.get("mode") or "status")
        if mode == "action":
            mode = "action_card"
        if mode == "countdown":
            result = self.runtime.projection.render_countdown(
                title,
                int(payload.get("duration_seconds") or payload.get("seconds") or 300),
                message=str(payload.get("message") or ""),
            )
        elif mode in {"confirmation", "action_card"}:
            result = self.runtime.projection.render_action_card(
                title,
                list_string(payload.get("actions") or payload.get("message")),
                decisions=list_string(payload.get("decisions")),
            )
        elif mode in {"status", "status_card"}:
            result = self.runtime.projection.render_status_card(
                title,
                str(payload.get("message") or payload.get("status") or "ready"),
                details=list_string(payload.get("details")),
                accent=str(payload.get("accent") or "blue"),
            )
        else:
            result = self.runtime.projection.render_markdown(title, str(payload.get("body") or payload.get("message") or ""), mode)
        task = self.create_task("投影卡片", "projection", "completed", {"title": title, "mode": mode}, result)
        self.record_audit("projection_card", "ok", str(result.get("path")), {"title": title, "mode": mode, "task_id": task["task_id"]}, ctx)
        return {"status": "completed", "task_id": task["task_id"], **result}

    def api_projection_markdown_file(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(require_file_path(payload), ctx, action="projection_markdown_file")
        suffix = safe.path.suffix.lower()
        if suffix not in {".md", ".markdown", ".txt"}:
            self.record_audit(
                "projection_markdown_file",
                "blocked",
                safe.workspace_name,
                {"reason": "unsupported_suffix", "suffix": suffix},
                ctx,
            )
            raise ApiError(
                "unsupported_projection_file",
                "Only Markdown or text files can be projected directly. For PPT, use the browser screen-capture PPT summary flow.",
                status=415,
                details={"suffix": suffix, "supported_suffixes": [".md", ".markdown", ".txt"]},
            )
        if safe.path.stat().st_size > 1_000_000:
            self.record_audit(
                "projection_markdown_file",
                "blocked",
                safe.workspace_name,
                {"reason": "file_too_large", "size_bytes": safe.path.stat().st_size},
                ctx,
            )
            raise ApiError("projection_file_too_large", "Projection Markdown/text file must be 1 MB or smaller.", status=413)
        try:
            body = safe.path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            self.record_audit("projection_markdown_file", "blocked", safe.workspace_name, {"reason": "not_utf8"}, ctx)
            raise ApiError("unsupported_projection_file", "Projection Markdown/text file must be UTF-8.", status=415) from exc
        title = str(payload.get("title") or safe.path.stem or "Workspace Markdown")
        result = self.runtime.projection.render_markdown(
            title,
            redact_projection_text(body),
            mode="markdown_file",
        )
        output_path = str(result.get("path") or "")
        response = {
            "status": "completed",
            "source_workspace_name": safe.workspace_name,
            "source_path": str(safe.path),
            "chars": len(body),
            **result,
            "projection_path": output_path,
            "preview_url": self._projection_preview_url,
        }
        task = self.create_task("投影 Markdown 文件", "projection", "completed", {"file_path": safe.workspace_name, "title": title}, response)
        response["task_id"] = task["task_id"]
        self.record_audit(
            "projection_markdown_file",
            "ok",
            safe.workspace_name,
            {"task_id": task["task_id"], "projection_path": output_path, "chars": len(body)},
            ctx,
        )
        return response

    def api_projection_pptx_session_status(self, params: dict[str, list[str]], ctx: RequestContext) -> dict[str, object]:
        file_path = str(params.get("file_path", [""])[0] or "").strip()
        if not file_path:
            raise ApiError("missing_file_path", "Missing file_path.", status=400)
        safe = self.ensure_allowed_path(file_path, ctx, action="projection_pptx_session_status")
        if safe.path.suffix.lower() != ".pptx":
            raise ApiError("unsupported_file_type", "PPT projection requires a .pptx file.", status=400, details={"suffix": safe.path.suffix.lower()})
        slides = self.runtime.projection.extract_pptx_slides(safe.path)
        output = self.build_pptx_session_payload(
            title=str(params.get("title", [""])[0] or safe.path.stem),
            safe=safe,
            slides=slides,
            slide_index=safe_int(params.get("slide_index", ["1"])[0], 1),
            projection=None,
            status="ready",
        )
        self.record_audit("projection_pptx_session_status", "ok", safe.workspace_name, {"slide_count": len(slides)}, ctx)
        return output

    def api_projection_pptx_session(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        safe = self.ensure_allowed_path(require_file_path(payload), ctx, action="projection_pptx_session")
        if safe.path.suffix.lower() != ".pptx":
            self.record_audit("projection_pptx_session", "blocked", safe.workspace_name, {"reason": "unsupported_suffix", "suffix": safe.path.suffix.lower()}, ctx)
            raise ApiError("unsupported_file_type", "PPT projection requires a .pptx file.", status=400, details={"suffix": safe.path.suffix.lower()})
        slides = self.runtime.projection.extract_pptx_slides(safe.path)
        if not slides:
            self.record_audit("projection_pptx_session", "blocked", safe.workspace_name, {"reason": "no_extractable_slides"}, ctx)
            raise ApiError("empty_pptx", "No readable slides were found in this PPTX file.", status=400)
        title = str(payload.get("title") or safe.path.stem).strip() or safe.path.stem
        slide_index = safe_int(payload.get("slide_index"), 1)
        action = str(payload.get("action") or "show").strip().lower()
        if action == "next":
            slide_index += 1
        elif action == "previous":
            slide_index -= 1
        slide_index = max(1, min(len(slides), slide_index))
        projection = self.runtime.projection.render_pptx_slide(
            title,
            slides[slide_index - 1],
            slide_count=len(slides),
            source_name=safe.workspace_name,
        )
        output = self.build_pptx_session_payload(
            title=title,
            safe=safe,
            slides=slides,
            slide_index=slide_index,
            projection=projection,
            status="completed",
        )
        task = self.create_task(
            f"PPT 投影：{title}",
            "projection",
            "completed",
            {"file_path": safe.workspace_name, "slide_index": slide_index, "action": action},
            output,
        )
        output["task_id"] = task["task_id"]
        self.record_audit(
            "projection_pptx_session",
            "ok",
            str(projection.get("path")),
            {"task_id": task["task_id"], "source": safe.workspace_name, "slide_index": slide_index, "slide_count": len(slides)},
            ctx,
        )
        return output

    def api_projection_calibration_pattern(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        title = str(payload.get("title") or "投影校准测试图")
        result = self.runtime.projection.render_calibration_pattern(title)
        task = self.create_task("投影校准测试图", "projection", "completed", {"title": title}, result)
        self.record_audit("projection_calibration_pattern", "ok", str(result.get("path")), {"task_id": task["task_id"]}, ctx)
        return {"task_id": task["task_id"], **result}

    def api_projection_calibration_analyze(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        image_data_url = require_string(payload, "image_data_url")
        title = str(payload.get("title") or "projection_calibration")
        try:
            result = self.runtime.projection.analyze_calibration_capture(
                image_data_url,
                title=title,
                max_bytes=self.max_upload_bytes,
            )
        except ValueError as exc:
            raise ApiError("invalid_image", str(exc), status=400) from exc
        status = normalize_task_status(str(result.get("status") or "completed"))
        task = self.create_task("投影校准分析", "projection", status, {"title": title}, result)
        self.record_audit("projection_calibration_analyze", status_to_audit(status), str(result.get("capture_path") or title), {"task_id": task["task_id"]}, ctx)
        return {"task_id": task["task_id"], **result}

    def api_projection_calibration_apply(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        calibration = payload.get("calibration") if isinstance(payload.get("calibration"), dict) else {}
        if not calibration:
            analysis_path_value = str(payload.get("analysis_path") or "").strip()
            if not analysis_path_value:
                raise ApiError("missing_calibration", "Provide calibration payload or analysis_path.", status=400)
            analysis_path = Path(analysis_path_value).expanduser().resolve()
            projection_dir = self.runtime.config.projection_dir.resolve()
            if not analysis_path.is_file() or not analysis_path.is_relative_to(projection_dir):
                raise ApiError("blocked", "Calibration analysis must stay inside projection output directory.", status=403)
            try:
                loaded = json.loads(analysis_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ApiError("invalid_calibration", "Calibration analysis file is not valid JSON.", status=400) from exc
            calibration = loaded if isinstance(loaded, dict) else {}
        profile = build_display_profile(
            ambient_lux=optional_float(payload.get("ambient_lux")),
            calibration=calibration,
            mode="calibration",
            brightness=optional_float(payload.get("brightness")),
            contrast=optional_float(payload.get("contrast")),
            scale=optional_float(payload.get("scale")),
            keystone_x=optional_float(payload.get("keystone_x")),
            keystone_y=optional_float(payload.get("keystone_y")),
        )
        saved = save_display_profile(self.projection_display_profile_path(), profile)
        recommendations = calibration.get("recommendations") if isinstance(calibration.get("recommendations"), list) else []
        result = {
            "status": "completed",
            "profile": saved,
            "path": str(self.projection_display_profile_path()),
            "preview_url": self._projection_preview_url,
            "calibration_status": calibration.get("status"),
            "recommendations": recommendations,
            "hardware_control": calibration.get("hardware_control", "display_substitute_digital_profile"),
            "message": "Calibration profile applied to the external-monitor preview. Physical focus/keystone motors still require projector SDK integration.",
        }
        task = self.create_task("投影校准自动应用", "projection", "completed", {"source": payload.get("analysis_path") or "payload"}, result)
        self.record_audit("projection_calibration_apply", "ok", "projection_display_profile", {"task_id": task["task_id"], "profile": saved}, ctx)
        return {"task_id": task["task_id"], **result}

    def api_projection_display_profile(self, ctx: RequestContext | None = None) -> dict[str, object]:
        profile = ProjectionPreviewServer(
            self.runtime.config.projection_dir,
            self.audit,
            display_profile_path=self.projection_display_profile_path(),
        ).load_display_profile()
        hardware = probe_hardware(self.runtime.config, projection_preview_port=self.projection_preview_port)
        devices = hardware.get("devices") if isinstance(hardware.get("devices"), dict) else {}
        projection = devices.get("projection") if isinstance(devices.get("projection"), dict) else {}
        details = projection.get("details") if isinstance(projection.get("details"), dict) else {}
        payload = {
            "status": "completed",
            "profile": profile.as_dict(),
            "path": str(self.projection_display_profile_path()),
            "preview_url": self._projection_preview_url,
            "display_test_mode": True,
            "physical_projector": "connected" if details.get("projector_connected") else "display_substitute",
            "projector_output": details.get("projector_output", ""),
            "message": "数字显示配置已保存；若接入真实投影仪，将同步显示物理投影状态。",
        }
        if ctx:
            self.record_audit("projection_display_profile.status", "ok", "projection_display_profile", {"mode": profile.mode}, ctx)
        return payload

    def api_projection_display_profile_update(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        mode = str(payload.get("mode") or "manual")
        ambient_lux = optional_float(payload.get("ambient_lux"))
        calibration_payload = payload.get("calibration") if isinstance(payload.get("calibration"), dict) else None
        profile = build_display_profile(
            ambient_lux=ambient_lux,
            calibration=calibration_payload,
            mode=mode,
            brightness=optional_float(payload.get("brightness")),
            contrast=optional_float(payload.get("contrast")),
            scale=optional_float(payload.get("scale")),
            keystone_x=optional_float(payload.get("keystone_x")),
            keystone_y=optional_float(payload.get("keystone_y")),
        )
        saved = save_display_profile(self.projection_display_profile_path(), profile)
        result = {
            "status": "completed",
            "profile": saved,
            "path": str(self.projection_display_profile_path()),
            "preview_url": self._projection_preview_url,
            "message": "Display profile updated. The external-monitor preview refreshes automatically.",
        }
        self.record_audit("projection_display_profile.update", "ok", "projection_display_profile", result, ctx)
        return result

    def api_projection_summarize_ppt_page(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        pptx_file_path = str(payload.get("file_path") or payload.get("pptx_file_path") or "").strip()
        if pptx_file_path:
            return self.api_projection_summarize_pptx_page(payload, ctx, pptx_file_path=pptx_file_path)

        has_openai_vision = bool(self.runtime.config.openai_api_key)
        has_dashscope_vision = bool(getattr(self.runtime.config, "dashscope_api_key", ""))
        if not has_openai_vision and not has_dashscope_vision:
            result = {
                "status": "backend_missing",
                "message": "OPENAI_API_KEY or DASHSCOPE_API_KEY is required for PPT page image understanding.",
                "provider": "ResponsesLLM",
            }
            task = self.create_task("总结这一页 PPT", "projection", "backend_missing", {"source": payload.get("source") or "browser_capture"}, result)
            self.record_audit("projection_ppt_page_summary", "backend_missing", "ppt_page", {"task_id": task["task_id"]}, ctx)
            return {"task_id": task["task_id"], **result}

        image_data_url = require_string(payload, "image_data_url")
        title = str(payload.get("title") or "PPT 当前页总结")
        render_projection = bool(payload.get("render_projection", True))
        screenshot_path = self.write_ppt_page_capture(image_data_url, title)
        prompt = "\n".join(
            [
                "请总结这张 PPT 当前页截图。",
                "输出中文 Markdown，必须包含：",
                "1. 一句话结论",
                "2. 页面核心要点",
                "3. 可直接口播的演讲提示",
                "4. 待确认/看不清的信息",
                "不要编造截图中没有的信息；看不清时写“待确认”。",
            ]
        )
        try:
            summary, provider, model = self._complete_ppt_page_summary(prompt, image_data_url, payload)
        except LLMError as exc:
            result = {
                "status": "backend_missing",
                "message": str(exc),
                "provider": "vision_llm",
                "screenshot_path": str(screenshot_path),
            }
            task = self.create_task("总结这一页 PPT", "projection", "backend_missing", {"source": payload.get("source") or "browser_capture"}, result)
            self.record_audit("projection_ppt_page_summary", "backend_missing", str(screenshot_path), {"task_id": task["task_id"], "error": str(exc)[:500]}, ctx)
            return {"task_id": task["task_id"], **result}

        summary_path = self.runtime.workspace.write_text(
            safe_filename(title, default="ppt_page", suffix="_summary.md"),
            summary,
            action="projection.ppt_page_summary_write",
        )
        projection = None
        if render_projection:
            projection = self.runtime.projection.render_markdown(title, summary, mode="ppt_page_summary")
        result = {
            "status": "completed",
            "summary": summary,
            "summary_path": str(summary_path),
            "screenshot_path": str(screenshot_path),
            "projection": projection,
            "projection_path": str(projection.get("path") if isinstance(projection, dict) else ""),
            "provider": provider,
            "model": model,
        }
        task = self.create_task("总结这一页 PPT", "projection", "completed", {"source": payload.get("source") or "browser_capture", "title": title}, result)
        self.record_audit("projection_ppt_page_summary", "ok", str(screenshot_path), {"task_id": task["task_id"], "summary_path": str(summary_path), "projection_path": result["projection_path"]}, ctx)
        return {"task_id": task["task_id"], **result}

    def api_projection_summarize_pptx_page(self, payload: dict[str, Any], ctx: RequestContext, *, pptx_file_path: str) -> dict[str, object]:
        safe = self.ensure_allowed_path(pptx_file_path, ctx, action="projection_pptx_page_summary")
        if safe.path.suffix.lower() != ".pptx":
            self.record_audit("projection_pptx_page_summary", "blocked", safe.workspace_name, {"reason": "unsupported_suffix", "suffix": safe.path.suffix.lower()}, ctx)
            raise ApiError("unsupported_file_type", "PPTX page summary requires a .pptx file.", status=400, details={"suffix": safe.path.suffix.lower()})
        slides = self.runtime.projection.extract_pptx_slides(safe.path)
        if not slides:
            self.record_audit("projection_pptx_page_summary", "blocked", safe.workspace_name, {"reason": "no_extractable_slides"}, ctx)
            raise ApiError("empty_pptx", "No readable slides were found in this PPTX file.", status=400)
        slide_index = max(1, min(len(slides), safe_int(payload.get("slide_index"), 1)))
        slide = slides[slide_index - 1]
        title = str(payload.get("title") or f"{safe.path.stem} 第 {slide_index} 页总结")
        slide_title = str(slide.get("title") or f"Slide {slide_index}")
        body = str(slide.get("text") or "").strip()
        bullets = [line.strip() for line in body.splitlines() if line.strip()]
        if not bullets and slide_title:
            bullets = [slide_title]
        summary_lines = [
            f"# {redact_projection_text(title)}",
            "",
            "Provider: local_pptx_text",
            f"Source: {redact_projection_text(safe.workspace_name)}",
            f"Slide: {slide_index}/{len(slides)}",
            "",
            "## 一句话结论",
            f"- {redact_projection_text(slide_title)}",
            "",
            "## 页面核心要点",
            *([f"- {redact_projection_text(item)}" for item in bullets[:8]] or ["- 此页没有可提取文本；请使用屏幕捕获总结视觉版式。"]),
            "",
            "## 可直接口播的演讲提示",
            f"- 这一页重点说明：{redact_projection_text(slide_title)}。",
            *[f"- 可以补充展开：{redact_projection_text(item)}" for item in bullets[1:4]],
            "",
            "## 待确认/看不清的信息",
            "- PPTX 文本模式无法读取图片、图表视觉细节和动画状态；需要时请使用屏幕捕获当前页。",
            "",
        ]
        summary = "\n".join(summary_lines)
        summary_path = self.runtime.workspace.write_text(
            safe_filename(title, default="pptx_page", suffix="_summary.md"),
            summary,
            action="projection.pptx_page_summary_write",
        )
        projection = None
        if bool(payload.get("render_projection", True)):
            projection = self.runtime.projection.render_markdown(title, summary, mode="pptx_page_summary")
        result = {
            "status": "completed",
            "summary": summary,
            "summary_path": str(summary_path),
            "screenshot_path": "",
            "source_workspace_name": safe.workspace_name,
            "source_path": str(safe.path),
            "slide_index": slide_index,
            "slide_count": len(slides),
            "current_slide": slide,
            "projection": projection,
            "projection_path": str(projection.get("path") if isinstance(projection, dict) else ""),
            "provider": "local_pptx_text",
            "model": "local_rules",
            "message": "已基于 PPTX 当前页可提取文本生成总结；视觉图表请使用屏幕捕获。",
        }
        task = self.create_task("总结 PPTX 当前页", "projection", "completed", {"file_path": safe.workspace_name, "slide_index": slide_index, "title": title}, result)
        self.record_audit(
            "projection_pptx_page_summary",
            "ok",
            safe.workspace_name,
            {"task_id": task["task_id"], "summary_path": str(summary_path), "projection_path": result["projection_path"], "slide_index": slide_index},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_projection_latest(self, ctx: RequestContext | None = None) -> dict[str, object]:
        latest = latest_projection_file(self.runtime.config.projection_dir)
        cards = self.projection_cards()
        if latest is None:
            return {"status": "empty", "path": None, "html": "", "cards": cards}
        text = latest.read_text(encoding="utf-8", errors="replace")
        payload = {
            "status": "ok",
            "name": latest.name,
            "path": str(latest),
            "mtime": latest.stat().st_mtime,
            "html": markdown_to_html(text),
            "cards": cards,
        }
        if ctx:
            self.record_audit("projection_latest", "ok", latest.name, {"mtime": payload["mtime"]}, ctx)
        return payload

    def api_projection_service_status(self) -> dict[str, object]:
        running = self.projection_preview_running()
        kiosk_running = self.projection_kiosk_running()
        hardware = probe_hardware(self.runtime.config, projection_preview_port=self.projection_preview_port)
        devices = hardware.get("devices") if isinstance(hardware.get("devices"), dict) else {}
        projection = devices.get("projection") if isinstance(devices.get("projection"), dict) else {}
        details = projection.get("details") if isinstance(projection.get("details"), dict) else {}
        projector_connected = bool(details.get("projector_connected"))
        status = "online" if running and (kiosk_running or not projector_connected) else "adapter_ready"
        if running and projector_connected and not kiosk_running:
            message = "投影预览服务在线，但全屏投影窗口未运行；投影仪可能仍显示桌面。"
        elif running and kiosk_running:
            message = "投影预览服务和全屏投影窗口均已运行。"
        else:
            message = "投影预览服务可用。若接入真实投影仪，将同时报告物理设备状态。"
        return {
            "status": status,
            "preview_url": self._projection_preview_url,
            "display_test_mode": True,
            "physical_projector": "connected" if projector_connected else "display_substitute",
            "projector_output": details.get("projector_output", ""),
            "output_target": "projector" if projector_connected else "external_monitor",
            "started_at": self._projection_preview_started_at,
            "kiosk_running": kiosk_running,
            "kiosk_started_at": self._projection_kiosk_started_at,
            "kiosk_pid": self._projection_kiosk_process.pid if self._projection_kiosk_process and self._projection_kiosk_process.poll() is None else None,
            "message": message,
        }

    def api_projection_service_start(self, ctx: RequestContext) -> dict[str, object]:
        result = self.start_projection_preview_service()
        self.record_audit("projection_start", status_to_audit(str(result["status"])), "projection_preview_service", result, ctx)
        return result

    def api_projection_service_stop(self, ctx: RequestContext) -> dict[str, object]:
        result = self.stop_projection_preview_service()
        self.record_audit("projection_stop", status_to_audit(str(result["status"])), "projection_preview_service", result, ctx)
        return result


GET = {
    "/api/projection/latest": "api_projection_latest",
    "/api/projection/service/status": "api_projection_service_status",
    "/api/projection/display-profile": "api_projection_display_profile",
}
POST = {
    "/api/projection/card": "api_projection_card",
    "/api/projection/markdown-file": "api_projection_markdown_file",
    "/api/projection/pptx/session": "api_projection_pptx_session",
    "/api/projection/summarize-ppt-page": "api_projection_summarize_ppt_page",
    "/api/projection/calibration/pattern": "api_projection_calibration_pattern",
    "/api/projection/calibration/analyze": "api_projection_calibration_analyze",
    "/api/projection/calibration/apply": "api_projection_calibration_apply",
    "/api/projection/display-profile": "api_projection_display_profile_update",
}


def dispatch_get(server: Any, path: str, params: dict[str, list[str]], ctx: Any) -> Any:
    if path == "/api/projection/pptx/session": return server.api_projection_pptx_session_status(params, ctx)
    if path == "/api/projection/service/status": return server.api_projection_service_status()
    method = GET.get(path)
    return NOT_HANDLED if method is None else getattr(server, method)(ctx)


def dispatch_post(server: Any, path: str, payload: dict[str, Any], ctx: Any) -> Any:
    if path == "/api/projection/service/start": return server.api_projection_service_start(ctx)
    if path == "/api/projection/service/stop": return server.api_projection_service_stop(ctx)
    return exact_payload(server, path, payload, ctx, POST)
