"""Autotest Service HTTP 面（部署/运维面，2335）。

与 tzcomm 服务面共享同一 AutotestService（同一 Jobs 池）：
- tzcomm 面：runner 上 client run/pause/step/resume 的本机通路（v1.3 §2 硬约束，不变）；
- HTTP 面：健康检查、状态轮询、调试命令的运维入口（不参与 Hub 触发链路，
  Hub 触发仍走 workflow_dispatch → runner 本机 client，v1.3 §4.3）。

启动：python3 -m autotest.server.http（端口 AUTOTEST_HTTP_PORT，默认 2335）。
"""
from __future__ import annotations

import os
import threading
from typing import Any, Optional

from fastapi import FastAPI, Response
from pydantic import BaseModel

import tzcomm

from .server import AutotestService

DEFAULT_HTTP_PORT = 2335


class SubmitRequest(BaseModel):
    manifest: str
    scenario: Optional[str] = None
    checker: Optional[str] = None
    checker_config: Optional[dict] = None
    clock_rate: Optional[float] = None


class CommandRequest(BaseModel):
    job_id: str
    command: str  # pause / step / resume
    n: int = 1


def create_app(service: AutotestService) -> FastAPI:
    """在既有 AutotestService 上挂 HTTP 路由（与 tzcomm 面共享 Jobs 池）。"""
    app = FastAPI(title="autotest-service", version="0.1.0")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "jobs": service.job_count}

    # 注意：job_status 成功时也带 "error": None 键，错误判定须用真值而非键存在
    @app.post("/api/submit")
    def submit(req: SubmitRequest, response: Response) -> dict:
        reply = service.submit(req.model_dump(exclude_none=True))
        if reply.get("error"):
            response.status_code = 400  # 配置类错误（manifest/场景/插件加载失败）
        return reply

    @app.post("/api/command")
    def command(req: CommandRequest, response: Response) -> dict:
        reply = service.command(req.job_id, req.command, req.n)
        if reply.get("error"):
            response.status_code = 404 if "未知 job_id" in reply["error"] else 409
        return reply

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str, response: Response) -> dict:
        reply = service.job_status(job_id)
        if reply.get("error"):
            response.status_code = 404
        return reply

    return app


def main() -> None:
    # 接管 tzcomm 诊断日志（与 server.main 一致）
    tzcomm.setup_logging()
    service = AutotestService()
    # tzcomm 面后台 spin；HTTP 面占主线程（uvicorn 管理信号与优雅退出）
    threading.Thread(target=service.spin, daemon=True).start()

    port = int(os.environ.get("AUTOTEST_HTTP_PORT", DEFAULT_HTTP_PORT))
    import uvicorn

    try:
        uvicorn.run(create_app(service), host="0.0.0.0", port=port)
    finally:
        service.close()


if __name__ == "__main__":
    main()
