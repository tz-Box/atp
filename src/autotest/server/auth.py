"""M-E11 飞书 OAuth 登录（通路复用 Hub/PMS 已验证实现，见 tz_cicd_hub hub/routers/auth.py）。

定位：**人**的会话凭证，与 ATP_SERVICE_TOKEN（**机器**间，Hub→ATP）分层并存——
console 读 = 成员会话（member/admin），写（手动触发评测）= 管理员会话（admin）；
Hub 机器调用仍走 Bearer service-token，不受本模块影响。

通路（与 Hub 同路径约定）：
  GET  /api/auth/options          → {"oauth": 是否已配置}（公开）
  GET  /api/auth/feishu/start     → 302 飞书授权页（state 10min 有效）
  GET  /api/auth/feishu/callback  → code 换 user_access_token → user_info → 成员门 → 建会话 → /console
  GET  /api/auth/me               → 当前会话 {"name","open_id","role"} 或 401
  POST /api/auth/logout           → 清会话

白名单（Hub members 表的 ATP 简化）：~/.config/autotest/console_users.json（chmod 600）
  {"users": [{"name": "张三", "oauth_open_id": "ou_xxx"(可空), "role": "admin|member"}]}
匹配顺序与 Hub 同：oauth_open_id 直中 → 姓名唯一匹配（命中回填 open_id 写回文件）→ 拒绝。
OpenID 按应用隔离：OAuth 返回的 open_id 与 lark-cli 应用域 open_id 不同，故需此回填机制。

会话落 ~/.config/autotest/sessions.json（原子写、600，重启不失效），
cookie atp_session（HttpOnly/SameSite=Lax/14 天）。

前置：atp.env 配 FEISHU_APP_ID / FEISHU_APP_SECRET（O3 分发，与 Hub/PMS 同一只自建应用即可）；
飞书后台登记回调地址，形如 http://atp.turing-zero.com/api/auth/feishu/callback
（redirect_uri 按请求 Host 生成，域名/IP 直连各自登记）。

出站走 urllib（与 callback.py 一致，零新依赖）；同步端点由 FastAPI 线程池执行。
"""
from __future__ import annotations

import json
import logging
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

log = logging.getLogger("atp.auth")
router = APIRouter()

AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
USERINFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"

SESSION_COOKIE = "atp_session"
SESSION_TTL_DAYS = 14

# 模块级路径（单测 monkeypatch 注入 tmp_path）
USERS_PATH = Path.home() / ".config" / "autotest" / "console_users.json"
SESSIONS_PATH = Path.home() / ".config" / "autotest" / "sessions.json"

_states: dict[str, datetime] = {}      # oauth state -> 过期时刻（内存，重启重登即可）
_sessions: dict[str, dict] = {}        # token -> {name, open_id, role, expires}


# ---------- 会话存取 ----------

def _load_sessions() -> None:
    if SESSIONS_PATH.exists():
        try:
            _sessions.update(json.loads(SESSIONS_PATH.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            log.warning("sessions.json 损坏，忽略重建")


def _save_sessions() -> None:
    SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SESSIONS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_sessions, ensure_ascii=False), encoding="utf-8")
    tmp.replace(SESSIONS_PATH)
    SESSIONS_PATH.chmod(0o600)


_load_sessions()


def current_user(request: Request) -> dict | None:
    """会话有效 → {"name","open_id","role",...}；无效/过期 → None。http.py 人通道鉴权用。"""
    token = request.cookies.get(SESSION_COOKIE, "")
    s = _sessions.get(token)
    if not s:
        return None
    if datetime.fromisoformat(s["expires"]) < datetime.now():
        _sessions.pop(token, None)
        _save_sessions()
        return None
    return s


def _new_session(name: str, open_id: str, role: str) -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = {"name": name, "open_id": open_id, "role": role,
                        "expires": (datetime.now() + timedelta(days=SESSION_TTL_DAYS)).isoformat()}
    _save_sessions()
    return token


# ---------- 白名单（成员门） ----------

def _load_users() -> list[dict]:
    if not USERS_PATH.exists():
        return []
    try:
        return json.loads(USERS_PATH.read_text(encoding="utf-8")).get("users") or []
    except (ValueError, OSError):
        log.warning("console_users.json 损坏，按空白名单处理")
        return []


def _save_users(users: list[dict]) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"users": users}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USERS_PATH)
    USERS_PATH.chmod(0o600)


def match_member(open_id: str, name: str) -> dict | None:
    """oauth_open_id 直中 → 姓名唯一匹配（回填）→ None（拒绝）。与 Hub 同范式。"""
    users = _load_users()
    for u in users:
        if u.get("oauth_open_id") and u["oauth_open_id"] == open_id:
            return u
    cands = [u for u in users if u.get("name") == name]
    if len(cands) == 1:
        cands[0]["oauth_open_id"] = open_id
        _save_users(users)
        log.info("oauth_open_id 回填: %s", name)
        return cands[0]
    return None


# ---------- OAuth 流程 ----------

def _oauth_cfg() -> tuple[str, str]:
    import os
    return (os.environ.get("FEISHU_APP_ID", "").strip(),
            os.environ.get("FEISHU_APP_SECRET", "").strip())


def _callback_uri(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}/api/auth/feishu/callback"


def _login_fail(msg: str) -> RedirectResponse:
    return RedirectResponse("/console?login_error=" + urllib.parse.quote(msg), status_code=302)


def _http_json(url: str, *, method: str = "GET",
               headers: dict | None = None, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


@router.get("/api/auth/options")
def auth_options() -> dict:
    """登录页用：告知前端是否已配置飞书授权登录（公开，无敏感信息）。"""
    app_id, secret = _oauth_cfg()
    return {"oauth": bool(app_id and secret)}


@router.get("/api/auth/me")
def auth_me(request: Request):
    u = current_user(request)
    if not u:
        return Response(status_code=401)
    return {"name": u["name"], "open_id": u["open_id"], "role": u.get("role") or "member"}


@router.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE, "")
    if token in _sessions:
        _sessions.pop(token, None)
        _save_sessions()
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/api/auth/feishu/start")
def auth_feishu_start(request: Request) -> RedirectResponse:
    app_id, secret = _oauth_cfg()
    if not (app_id and secret):
        return _login_fail("ATP 未配置飞书登录（FEISHU_APP_ID/SECRET），请用机器 token")
    state = secrets.token_urlsafe(16)
    _states[state] = datetime.now() + timedelta(minutes=10)
    query = urllib.parse.urlencode({
        "client_id": app_id,
        "redirect_uri": _callback_uri(request),
        "response_type": "code",
        "state": state,
    })
    return RedirectResponse(f"{AUTHORIZE_URL}?{query}", status_code=302)


@router.get("/api/auth/feishu/callback")
def auth_feishu_callback(request: Request, code: str = "", state: str = "",
                         error: str = "") -> RedirectResponse:
    if error or not code:
        return _login_fail(f"飞书授权未完成({error or '缺少授权码'})，请重试")
    exp = _states.pop(state, None)
    if not exp or exp < datetime.now():
        return _login_fail("授权会话已过期（或服务重启过），请重新点击飞书登录")

    app_id, secret = _oauth_cfg()
    try:
        tok = _http_json(TOKEN_URL, method="POST", payload={
            "grant_type": "authorization_code",
            "client_id": app_id, "client_secret": secret,
            "code": code, "redirect_uri": _callback_uri(request),
        })
        access_token = tok.get("access_token")
        if not access_token:
            detail = tok.get("error_description") or tok.get("error") or str(tok.get("code"))
            return _login_fail(f"换取用户凭证失败:{detail}（请检查 App Secret 与重定向 URL 配置）")
        info = (_http_json(USERINFO_URL,
                           headers={"Authorization": f"Bearer {access_token}"})
                .get("data") or {})
    except Exception as e:  # noqa: BLE001 — 网络/证书异常统一回登录页
        log.warning("飞书通信异常: %s", e)
        return _login_fail(f"与飞书通信失败:{e}")

    open_id, name = (info.get("open_id") or "").strip(), (info.get("name") or "").strip()
    if not open_id:
        return _login_fail("未能获取飞书用户身份（应用需开通「获取用户基本信息」权限）")
    # 成员门：console_users.json 即白名单。oauth_open_id 直中 → 姓名唯一匹配（回填）→ 拒绝
    m = match_member(open_id, name)
    if not m:
        log.info("登录拒绝: %s(%s) 未在白名单", name, open_id)
        return _login_fail(f"「{name}」未在 ATP 控制台白名单，请联系管理员在评测机 "
                           f"~/.config/autotest/console_users.json 添加后再登录")

    token = _new_session(name, open_id, m.get("role") or "member")
    log.info("登录成功: %s(role=%s)", name, m.get("role"))
    resp = RedirectResponse("/console", status_code=302)
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                    max_age=SESSION_TTL_DAYS * 86400)
    return resp
