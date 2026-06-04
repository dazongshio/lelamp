from __future__ import annotations

from dataclasses import dataclass

from .audit import AuditLogger
from .config import OfficeAgentConfig
from .camera_observer import CameraObserverService
from .daily import LocalDailyService
from .desktop import DesktopService
from .desktop_automation import BrowserAutomationService
from .desktop_tasks import DesktopTaskQueue
from .documents import DocumentService
from .environment import EnvironmentSensingService
from .enterprise import EnterprisePolicyService
from .file_search import LocalFileSearchService
from .intent_router import OfficeIntentRouter
from .lelamp_experience import LeLampExperienceService
from .meeting import MeetingService
from .memory import MemoryService
from .mobile_bridge import MobileBridgeConfig, MobileBridgeService
from .p0 import P0OfficeService
from .projection import ProjectionService
from .scanning import ScanService
from .scene import SceneService
from .screen import ScreenContextService
from .skills import SkillRegistry
from .smart_home import SmartHomeConfig, SmartHomeService, parse_entity_map
from .task_planner import TaskPlanner
from .workspace import Workspace
from .xiaoai import XiaoAiService


@dataclass
class OfficeRuntime:
    config: OfficeAgentConfig
    audit: AuditLogger
    workspace: Workspace
    skills: SkillRegistry
    meeting: MeetingService
    documents: DocumentService
    scanning: ScanService
    projection: ProjectionService
    scene: SceneService
    memory: MemoryService
    desktop: DesktopService
    browser_automation: BrowserAutomationService
    desktop_tasks: DesktopTaskQueue
    daily: LocalDailyService
    file_search: LocalFileSearchService
    screen: ScreenContextService
    camera_observer: CameraObserverService
    environment: EnvironmentSensingService
    enterprise: EnterprisePolicyService
    lelamp_experience: LeLampExperienceService
    smart_home: SmartHomeService
    mobile_bridge: MobileBridgeService
    xiaoai: XiaoAiService
    p0: P0OfficeService
    planner: TaskPlanner
    intent_router: OfficeIntentRouter

    def security_status(self) -> dict[str, object]:
        """Return the active local safety boundary for CLI/tests/agents."""
        self.audit.record("security.status")
        return {
            "permission_mode": self.config.permission_mode.value,
            "workspace_dir": str(self.config.workspace_dir),
            "allowed_roots": [str(path) for path in self.config.allowed_roots],
            "audit_log_path": str(self.config.audit_log_path),
            "projection_dir": str(self.config.projection_dir),
            "shared_inbox_dir": str(self.config.workspace_dir / "shared_inbox"),
            "memory_path": str(self.config.memory_path),
            "desktop_backend": self.config.desktop_backend,
            "hardware_enabled": self.config.enable_hardware,
            "meeting_mode_enabled": self.meeting.status()["meeting_mode_enabled"],
            "smart_home_provider": self.config.smart_home_provider,
            "mobile_bridge_status": self.mobile_bridge.status(),
            "enterprise_policy": self.enterprise.status(),
        }

    def readiness_report(self) -> dict[str, object]:
        from .readiness import build_readiness_report

        return build_readiness_report(self)


def build_runtime(config: OfficeAgentConfig | None = None) -> OfficeRuntime:
    config = config or OfficeAgentConfig.from_env()
    audit = AuditLogger(config.audit_log_path)
    workspace = Workspace(config.workspace_dir, config.allowed_roots, audit)
    skills = SkillRegistry(workspace, audit, config.permission_mode)
    meeting = MeetingService(workspace, audit, enabled=config.meeting_mode_enabled)
    projection = ProjectionService(config.projection_dir, audit)
    scene = SceneService(audit)
    daily = LocalDailyService(workspace, audit)
    file_search = LocalFileSearchService(workspace, audit, config.allowed_roots)
    screen = ScreenContextService(workspace, audit)
    camera_observer = CameraObserverService(workspace=workspace, audit=audit, scene=scene)
    environment = EnvironmentSensingService(audit=audit, scene=scene)
    lelamp_experience = LeLampExperienceService(audit=audit, scene=scene, projection=projection)
    return OfficeRuntime(
        config=config,
        audit=audit,
        workspace=workspace,
        skills=skills,
        meeting=meeting,
        documents=DocumentService(workspace, audit, config),
        scanning=ScanService(workspace, audit, config),
        projection=projection,
        scene=scene,
        memory=MemoryService(config.memory_path, audit),
        desktop=DesktopService(
            audit,
            permission_mode=config.permission_mode,
            backend=config.desktop_backend,
            allowed_roots=config.allowed_roots,
            workspace_dir=config.workspace_dir,
        ),
        browser_automation=BrowserAutomationService(workspace, audit, config),
        desktop_tasks=DesktopTaskQueue(workspace, audit),
        daily=daily,
        file_search=file_search,
        screen=screen,
        camera_observer=camera_observer,
        environment=environment,
        enterprise=EnterprisePolicyService(config=config, audit=audit),
        lelamp_experience=lelamp_experience,
        smart_home=SmartHomeService(
            audit,
            SmartHomeConfig(
                provider=config.smart_home_provider,
                home_assistant_url=config.home_assistant_url,
                home_assistant_token=config.home_assistant_token,
                webhook_url=config.smart_home_webhook_url,
                entity_map=parse_entity_map(config.smart_home_entities),
            ),
        ),
        mobile_bridge=MobileBridgeService(
            audit,
            MobileBridgeConfig(
                webhook_url=config.mobile_bridge_webhook_url,
                shared_secret=config.mobile_bridge_shared_secret,
                device_id=config.mobile_bridge_device_id,
            ),
        ),
        xiaoai=XiaoAiService(audit),
        p0=P0OfficeService(
            workspace=workspace,
            audit=audit,
            meeting=meeting,
            projection=projection,
            daily=daily,
            file_search=file_search,
            screen=screen,
            config=config,
        ),
        planner=TaskPlanner(audit),
        intent_router=OfficeIntentRouter(audit),
    )
