from __future__ import annotations
from typing import Any
from ..target_validation import build_target_validation_report, run_target_validation
from ..product_checklist import build_product_checklist
from ._base import ApiError, NOT_HANDLED, RequestContext, exact_payload

def _helper(name:str):
    from .. import web_console
    return getattr(web_console,name)
def atomic_write_json(*a,**kw): return _helper("atomic_write_json")(*a,**kw)
def desktop_full_control_evidence(*a,**kw): return _helper("desktop_full_control_evidence")(*a,**kw)
def desktop_full_control_remediation(*a,**kw): return _helper("desktop_full_control_remediation")(*a,**kw)
def list_string(*a,**kw): return _helper("list_string")(*a,**kw)
def normalize_task_status(*a,**kw): return _helper("normalize_task_status")(*a,**kw)
def require_string(*a,**kw): return _helper("require_string")(*a,**kw)
def status_to_audit(*a,**kw): return _helper("status_to_audit")(*a,**kw)

class SystemRoutesMixin:
    def api_test_run(self, payload: dict[str, Any]) -> dict[str, object]:
        test_id = require_string(payload, "test_id")
        if test_id == "all":
            requested = list_string(payload.get("test_ids")) or list(_helper("CONSOLE_SAFE_TEST_IDS"))
            results = [self.run_console_test(item) for item in requested]
            status = "ok" if all(item.get("status") != "error" for item in results) else "partial"
            return {"status": status, "count": len(results), "results": results}
        return self.run_console_test(test_id)

    def api_product_validation_status(self, ctx: RequestContext) -> dict[str, object]:
        result = build_target_validation_report(self.runtime, projection_preview_url=self._projection_preview_url, tingwu_provider=self.tingwu)
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        self.record_audit(
            "product_validation.status",
            status_to_audit(str(result.get("status") or "adapter_ready")),
            "target_validation",
            {"completed": summary.get("completed"), "adapter_ready": summary.get("adapter_ready")},
            ctx,
        )
        return result

    def api_product_validation_run(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        test_id = require_string(payload, "test_id")
        options = payload.get("options") if isinstance(payload.get("options"), dict) else payload
        result = run_target_validation(self.runtime, test_id, options, projection_preview_url=self._projection_preview_url, tingwu_provider=self.tingwu)
        report = result.get("report") if isinstance(result.get("report"), dict) else {}
        task = self.create_task(
            f"目标验收：{report.get('feature', test_id)}",
            "validation",
            normalize_task_status(str(result.get("status") or "adapter_ready")),
            {"test_id": test_id},
            result,
        )
        self.record_audit(
            "product_validation.run",
            status_to_audit(str(result.get("status") or "adapter_ready")),
            test_id,
            {"task_id": task["task_id"], "json_workspace_name": result.get("json_workspace_name")},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_product_validation_import_desktop_result(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        report = data.get("report") if isinstance(data.get("report"), dict) else {}
        if not report and isinstance(data.get("data"), dict):
            nested = data["data"]
            report = nested.get("report") if isinstance(nested.get("report"), dict) else {}
        if not isinstance(report, dict) or report.get("id") != "desktop_full_control":
            raise ApiError("invalid_validation_result", "Expected a desktop_full_control validation result.", status=400)
        evidence = desktop_full_control_evidence(report)
        missing_evidence = [key for key, value in evidence.items() if not value]
        remediation = desktop_full_control_remediation(report, missing_evidence)
        result_status = "completed" if all(evidence.values()) and report.get("status") == "completed" else "adapter_ready"
        saved_payload = payload if payload.get("ok") is not None else {"ok": True, "data": data}
        report_dir = (self.runtime.config.workspace_dir / "validation_reports").resolve()
        report_dir.mkdir(parents=True, exist_ok=True)
        path = report_dir / "desktop_full_control_target_result.json"
        atomic_write_json(path, saved_payload)
        task = self.create_task(
            "导入 full_control 目标机验收结果",
            "validation",
            normalize_task_status(result_status),
            {"evidence": evidence, "missing_evidence": missing_evidence, "remediation": remediation},
            {
                "status": result_status,
                "workspace_name": self.workspace_relative_path(str(path)),
                "evidence": evidence,
                "missing_evidence": missing_evidence,
                "remediation": remediation,
            },
        )
        self.record_audit(
            "product_validation.import_desktop_result",
            status_to_audit(result_status),
            "desktop_full_control_target_result.json",
            {"task_id": task["task_id"], "evidence": evidence, "missing_evidence": missing_evidence, "remediation": remediation},
            ctx,
        )
        return {
            "status": result_status,
            "task_id": task["task_id"],
            "workspace_name": self.workspace_relative_path(str(path)),
            "path": str(path),
            "evidence": evidence,
            "missing_evidence": missing_evidence,
            "remediation": remediation,
        }

    def api_mobile_status(self, ctx: RequestContext) -> dict[str, object]:
        status = self.runtime.mobile_bridge.status()
        self.record_audit(
            "mobile_bridge.status",
            status_to_audit(str(status.get("status"))),
            "mobile_bridge",
            {"configured": status.get("configured"), "provider": status.get("provider")},
            ctx,
        )
        return status

    def api_mobile_request(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        request_text = require_string(payload, "request")
        authorized = bool(payload.get("authorized"))
        result = self.runtime.mobile_bridge.request(request_text, authorized=authorized)
        status = str(result.get("status") or "unknown")
        task_status = normalize_task_status(status)
        task = self.create_task("移动端桥接请求", "assistant", task_status, {"authorized": authorized}, result)
        self.record_audit(
            "mobile_bridge.web_request",
            status_to_audit(status),
            "mobile_bridge",
            {"task_id": task["task_id"], "status": status, "authorized": authorized},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

    def api_smart_home_status(self, ctx: RequestContext) -> dict[str, object]:
        status = self.runtime.smart_home.status()
        self.record_audit(
            "smart_home.status",
            status_to_audit(str(status.get("status"))),
            "smart_home",
            {
                "provider": status.get("provider"),
                "configured": status.get("configured"),
                "known_entities": len(status.get("known_entities") or []),
            },
            ctx,
        )
        return status

    def api_smart_home_control(self, payload: dict[str, Any], ctx: RequestContext) -> dict[str, object]:
        command = require_string(payload, "command")
        entity_name = str(payload.get("entity_name") or "").strip() or None
        result = self.runtime.smart_home.control(command, entity_name=entity_name)
        status = str(result.get("status") or "unknown")
        task_status = normalize_task_status(status)
        task = self.create_task("智能家居桥接请求", "assistant", task_status, {"entity_name": entity_name or ""}, result)
        self.record_audit(
            "smart_home.web_control",
            status_to_audit(status),
            "smart_home",
            {"task_id": task["task_id"], "status": status, "provider": result.get("provider")},
            ctx,
        )
        return {"task_id": task["task_id"], **result}

GET={"/api/product/validation/status":"api_product_validation_status", "/api/mobile/status":"api_mobile_status", "/api/smart-home/status":"api_smart_home_status"}
POST={"/api/product/validation/run":"api_product_validation_run", "/api/product/validation/import-desktop-result":"api_product_validation_import_desktop_result", "/api/mobile/request":"api_mobile_request", "/api/smart-home/control":"api_smart_home_control"}
def dispatch_get(server:Any,path:str,params:dict[str,list[str]],ctx:Any)->Any:
    if path=="/api/skills": return {"skills":server.runtime.skills.list_skills()}
    if path=="/api/p0": return server.runtime.p0.status()
    if path=="/api/readiness": return server.runtime.readiness_report()
    if path=="/api/product/checklist": return build_product_checklist(server.runtime)
    method=GET.get(path);return NOT_HANDLED if method is None else getattr(server,method)(ctx)
def dispatch_post(server:Any,path:str,payload:dict[str,Any],ctx:Any)->Any:
    if path=="/api/test/run": return server.api_test_run(payload)
    return exact_payload(server,path,payload,ctx,POST)
