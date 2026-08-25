"""Autotest Service HTTP 面（2335）。

与 tzcomm 服务面共享同一 AutotestService（同一 Jobs 池）：
- tzcomm 面：本机 client run/pause/step/resume 通路（v1.5 §3.3 同机约束，不变）；
- HTTP 面：健康检查、状态轮询、调试命令的运维入口 + **v1.5 §4.8 ATP 对外端点**
  （Hub 直连触发评测 / 状态轮询兜底 / 池化探活，M-E1 起逐批落地）。

启动：python3 -m autotest.server.http（端口 AUTOTEST_HTTP_PORT，默认 2335）。
"""
from __future__ import annotations

import hmac
import importlib.metadata
import os
import threading
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

import tzcomm

from ..commcheck import check_daemon
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


class EvaluationRequest(BaseModel):
    """v1.5 §4.8：Hub → ATP 评测提交载荷（坐标 only，评测逻辑来自 checkout 内容）。"""

    correlation_id: str = Field(min_length=1)  # Hub 对账锚点（chk_<ULID>），幂等键
    repo: str = Field(min_length=1)  # M-E1: 本地路径；M-E2: git URL（mirror 缓存 + worktree）
    ref: Optional[str] = None
    sha: Optional[str] = None  # 预留（Hub 已知 sha 时透传对账；实际 sha 以 checkout 回填为准）
    check_type: str = "autotest"  # 预留，恒 autotest
    scenario: Optional[str] = None  # manifest 相对仓根路径（缺省 scenario.yaml）
    save_baseline: bool = False  # 评测成功且该位置 true 时滚动基线（M-E3 生效）
    pms_task_id: Optional[str] = None  # 预留（PMS 任务锚点透传）


def require_service_token(authorization: str = Header(default="")) -> bool:
    """Hub→ATP Bearer 认证（对齐 PMS require_service_token 范式）：
    未配置 ATP_SERVICE_TOKEN → 503（端点关闭）；缺失/不符 → 401（compare_digest 防时序）。
    token 经 systemd Environment 注入；每次请求读 env，便于测试与轮换。
    """
    token = os.environ.get("ATP_SERVICE_TOKEN", "").strip()
    if not token:
        raise HTTPException(503, "ATP 服务端点未启用（未配置 ATP_SERVICE_TOKEN）")
    prefix = "Bearer "
    got = authorization[len(prefix):].strip() if authorization.startswith(prefix) else ""
    if not got or not hmac.compare_digest(got, token):
        raise HTTPException(401, "atp.service_token 无效或缺失")
    return True


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

    # ---- v1.5 §4.8 ATP 对外端点（Hub 直连通路，M-E1）----

    @app.post("/atp/evaluations", status_code=202)
    def submit_evaluation(req: EvaluationRequest, response: Response,
                          _: bool = Depends(require_service_token)) -> dict:
        if req.check_type != "autotest":
            response.status_code = 400
            return {"ok": False, "error": f"check_type 仅支持 autotest（预留）: {req.check_type!r}"}
        # cid 幂等：同 cid → 200 duplicate，不重复执行（Hub 重发/超时空转安全）
        existing = service.evaluations.get_by_cid(req.correlation_id)
        if existing is not None:
            response.status_code = 200
            return {"ok": True, "duplicate": True, "job_id": existing["job_id"]}
        reply = service.submit_evaluation(req.model_dump())
        if reply.get("error"):
            response.status_code = 400  # 坐标类错误（repo/ref 不可达、manifest 缺失）→ Hub 直接判 failure
            return {"ok": False, "error": reply["error"]}
        if reply.get("duplicate"):  # 并发同 cid 的 PK 兜底分支
            response.status_code = 200
            return {"ok": True, "duplicate": True, "job_id": reply["job_id"]}
        return {"ok": True, "job_id": reply["job_id"], "sha": reply.get("sha")}

    # ---- v1.5 §4.8 状态查询与探活（M-E4；探活必须即时响应，不被 M-E5 串行队列阻塞）----

    @app.get("/atp/evaluations/{job_id}")
    def evaluation_status(job_id: str, response: Response,
                          _: bool = Depends(require_service_token)) -> dict:
        row = service.evaluations.get_by_job_id(job_id)
        if row is None:
            response.status_code = 404
            return {"ok": False, "error": f"未知 job_id: {job_id!r}"}
        if row["status"] == "running":  # 含排队中（契约无 queued 态，对 Hub 即 running）
            return {"job_id": job_id, "status": "running"}
        return {
            "job_id": job_id,
            "status": row["status"],
            "sha": row["sha"],
            "report": {"summary": row["summary"], "run_url": None},  # run_url 语义归 Hub（v1.5）
            "finished_at": row["finished_at"],
        }

    @app.get("/atp/health")
    def atp_health() -> dict:
        # tzcomm daemon 快检（socket connect 带超时，不经评测队列）；ATP 无 tzcomm 即不可评测 → ok=False
        daemon = check_daemon(timeout=0.5)
        return {
            "ok": daemon.ok,
            "version": _atp_version(),
            "tzcomm": daemon.ok,
            "queue": service.queue_depth,
        }

    return app


def _atp_version() -> str:
    """包版本（单一事实源 pyproject；未安装环境退化为 dev）。"""
    try:
        return importlib.metadata.version("tz_atp")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


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
