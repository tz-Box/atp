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
import shutil
import threading
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import tzcomm

from ..commcheck import check_daemon
from ..registry import available_bodies, available_plugins
from . import auth
from .auth import current_user
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
    # M-F2（docs/03-scenario-schema.md §7）：null=全跑 | "场景id" | ["id",...]；
    # 路径值（含 / 或以 .yaml 结尾）保持旧语义 = manifest 相对仓根路径（缺省 scenario.yaml）
    scenario: Optional[Union[str, list[str]]] = None
    save_baseline: bool = False  # 评测成功且该位置 true 时滚动基线（M-E3 生效）
    pms_task_id: Optional[str] = None  # 预留（PMS 任务锚点透传）


def _bearer_ok(authorization: str) -> bool:
    """Bearer 命中已配置的 ATP_SERVICE_TOKEN（compare_digest 防时序；未配置 → False）。"""
    token = os.environ.get("ATP_SERVICE_TOKEN", "").strip()
    if not token:
        return False
    prefix = "Bearer "
    got = authorization[len(prefix):].strip() if authorization.startswith(prefix) else ""
    return bool(got) and hmac.compare_digest(got, token)


def require_service_token(authorization: str = Header(default="")) -> bool:
    """Hub→ATP Bearer 认证（对齐 PMS require_service_token 范式）：
    未配置 ATP_SERVICE_TOKEN → 503（端点关闭）；缺失/不符 → 401（compare_digest 防时序）。
    token 经 systemd Environment 注入；每次请求读 env，便于测试与轮换。
    """
    if not os.environ.get("ATP_SERVICE_TOKEN", "").strip():
        raise HTTPException(503, "ATP 服务端点未启用（未配置 ATP_SERVICE_TOKEN）")
    if not _bearer_ok(authorization):
        raise HTTPException(401, "atp.service_token 无效或缺失")
    return True


def require_reader(request: Request) -> bool:
    """读通道（M-E11 人/机分层）：Bearer（机器，Hub）**或** atp_session（人，console 成员）。
    两通道均未配置 → 503（端点关闭）；有配置但未通过 → 401。
    """
    if _bearer_ok(request.headers.get("authorization", "")):
        return True
    if current_user(request) is not None:
        return True
    _raise_auth_unavailable_or_401()
    return True  # unreachable


def require_writer(request: Request) -> bool:
    """写通道（M-E11）：Bearer（机器全权）**或** admin 会话；member 会话 → 403（只读）。"""
    if _bearer_ok(request.headers.get("authorization", "")):
        return True
    u = current_user(request)
    if u is not None:
        if u.get("role") == "admin":
            return True
        raise HTTPException(403, "只读成员（member）不可触发评测，请联系管理员提权")
    _raise_auth_unavailable_or_401()
    return True  # unreachable


def _raise_auth_unavailable_or_401() -> None:
    token_on = bool(os.environ.get("ATP_SERVICE_TOKEN", "").strip())
    oauth_on = all(auth._oauth_cfg())
    if not token_on and not oauth_on:
        raise HTTPException(503, "ATP 服务端点未启用（未配置 ATP_SERVICE_TOKEN / 飞书登录）")
    raise HTTPException(401, "未认证：请飞书登录或携带 Bearer token")


def create_app(service: AutotestService) -> FastAPI:
    """在既有 AutotestService 上挂 HTTP 路由（与 tzcomm 面共享 Jobs 池）。"""
    # Swagger 挪 /api/docs：/docs 让给算法工程师文档门户（ docs.html ）
    app = FastAPI(title="autotest-service", version="0.1.0",
                  docs_url="/api/docs", redoc_url=None)
    app.include_router(auth.router)  # M-E11 飞书登录（人通道，与 Bearer 机器通道分层并存）

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
                          _: bool = Depends(require_writer)) -> dict:  # M-E11: Bearer 或 admin 会话
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
            response.status_code = 400  # 坐标类错误（repo/ref 不可达、manifest 缺失、未知场景）→ Hub 直接判 failure
            body: dict = {"ok": False, "error": reply["error"]}
            if reply.get("code"):  # M-F3 机读错误码：manifest_missing/manifest_invalid/scenario_unknown
                body["code"] = reply["code"]
            return body
        if reply.get("duplicate"):  # 并发同 cid 的 PK 兜底分支
            response.status_code = 200
            return {"ok": True, "duplicate": True, "job_id": reply["job_id"]}
        return {"ok": True, "job_id": reply["job_id"], "sha": reply.get("sha")}

    # ---- v1.5 §4.8 状态查询与探活（M-E4；探活必须即时响应，不被 M-E5 串行队列阻塞）----

    @app.get("/atp/evaluations")
    def evaluation_list(limit: int = 50,
                        _: bool = Depends(require_reader)) -> dict:  # M-E11: Bearer 或会话
        """最近评测列表（M-E10 控制台数据面；ATP 本地运维端点，契约无需增补）。"""
        items = service.evaluations.list_recent(max(1, min(limit, 200)))
        return {"items": items}

    @app.get("/atp/evaluations/{job_id}")
    def evaluation_status(job_id: str, response: Response,
                          _: bool = Depends(require_reader)) -> dict:  # M-E11: Bearer 或会话
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

    @app.get("/atp/capabilities")
    def atp_capabilities() -> dict:
        """能力自报（M-E9a，无认证）：Hub 探活时采集，支撑 repo↔ATP 绑定管理面。

        plugins/bodies 扫描仓内目录（registry.available_*，静态事实，不受按需加载影响）；
        resources 为本机资源标签（gpu 经 nvidia-smi 探测；sensors 真机差异，预留配置声明）。
        """
        return {
            "version": _atp_version(),
            "plugins": available_plugins(),
            "bodies": available_bodies(),
            "resources": {"gpu": shutil.which("nvidia-smi") is not None, "sensors": []},
            "queue": service.queue_depth,
        }

    # ---- M-E10 Web 控制台（单页静态 HTML；数据端点均有 Bearer，页面本身无敏感数据）----

    @app.get("/console", response_class=HTMLResponse)
    def console() -> str:
        return (Path(__file__).with_name("console.html")).read_text(encoding="utf-8")

    # ---- 文档门户（面向算法工程师；仓内 docs/*.md 源文件直出，零构建零认证——内容非敏感）----

    @app.get("/docs", response_class=HTMLResponse)
    def docs_portal() -> str:
        return (Path(__file__).with_name("docs.html")).read_text(encoding="utf-8")

    @app.get("/docs/marked.min.js")
    def docs_marked() -> Response:
        # vendor 的 marked v12（仓内 docs_static/，避免内网依赖公网 CDN）
        return Response((Path(__file__).with_name("docs_static") / "marked.min.js")
                        .read_text(encoding="utf-8"), media_type="application/javascript")

    @app.get("/docs/api/index")
    def docs_index() -> dict:
        return {"items": _docs_index()}

    @app.get("/docs/md/{name}")
    def docs_md(name: str) -> Response:
        path = _docs_file(name)
        if path is None:
            raise HTTPException(404, f"未知文档: {name!r}")
        return Response(path.read_text(encoding="utf-8"),
                        media_type="text/markdown; charset=utf-8")

    return app


# 文档门户目录（src/autotest/server/http.py → parents[3] = 仓根）；仅顶层 *.md（scheme/ 为内部资料不暴露）
_DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"


def _docs_index() -> list[dict]:
    """文档清单（门户左侧导航）：文件名数字前缀即学习路径序（01- 02- …，新增文档选号落位即可，
    无需改服务端）；无前缀文件排在其后按名排序；title 取首个一级标题。"""
    files = sorted(_DOCS_DIR.glob("*.md"), key=lambda p: p.name)
    items = []
    for p in files:
        title = p.stem
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        items.append({"file": p.name, "title": title})
    return items


def _docs_file(name: str) -> Optional[Path]:
    """按文件名安全解析仓内 docs 顶层 md（防路径穿越；scheme/ 子目录不暴露）。"""
    if not name.endswith(".md") or "/" in name or ".." in name:
        return None
    path = (_DOCS_DIR / name).resolve()
    if path.parent != _DOCS_DIR.resolve() or not path.is_file():
        return None
    return path


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
