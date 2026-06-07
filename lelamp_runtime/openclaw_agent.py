from __future__ import annotations

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import AgentSession, RoomInputOptions
from livekit.plugins import noise_cancellation, openai

from lelamp.office_agent.agent import OpenClawOfficeAgent
from lelamp.office_agent.config import OfficeAgentConfig
from lelamp.office_agent.hardware import LampHardware
from lelamp.office_agent.runtime import build_runtime


load_dotenv()


async def entrypoint(ctx: agents.JobContext):
    config = OfficeAgentConfig.from_env()
    runtime = build_runtime(config)

    with LampHardware(
        enabled=config.enable_hardware,
        port=config.hardware_port,
        lamp_id=config.lamp_id,
        audit=runtime.audit,
        rgb_enabled=config.enable_rgb,
    ) as hardware:
        agent = OpenClawOfficeAgent(
            config=config,
            workspace=runtime.workspace,
            skills=runtime.skills,
            hardware=hardware,
            audit=runtime.audit,
            meeting=runtime.meeting,
            documents=runtime.documents,
            scanning=runtime.scanning,
            projection=runtime.projection,
            scene=runtime.scene,
            memory=runtime.memory,
            desktop=runtime.desktop,
            daily=runtime.daily,
            file_search=runtime.file_search,
            screen=runtime.screen,
            camera_observer=runtime.camera_observer,
            environment=runtime.environment,
            lelamp_experience=runtime.lelamp_experience,
            lelamp_voice=runtime.lelamp_voice,
            smart_home=runtime.smart_home,
            xiaoai=runtime.xiaoai,
            p0=runtime.p0,
            planner=runtime.planner,
        )

        session = AgentSession(
            llm=openai.realtime.RealtimeModel(
                voice="ballad",
            )
        )

        await session.start(
            room=ctx.room,
            agent=agent,
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        )

        await session.generate_reply(
            instructions=(
                "用中文简短说明你已经以 OpenClaw 沙箱办公代理启动，"
                "并提示用户可以导入文件、创建会议模板或查看安全状态。"
            )
        )


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint, num_idle_processes=1))
