"""协议消息信封与消息类型：协议单一来源。

所有跨进程消息都打包为 Message（可 msgpack 序列化）；传输层只负责收发 dict，不感知语义。
v1.1 §5 冻结：INIT 加 body_profile 下发；数据消息统一走 data 信封（schema/v/enc/blob）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

INIT = "init"
READY = "ready"
RESET = "reset"
RESET_ACK = "reset_ack"
STEP = "step"
RESULT = "result"
ACTION = "action"
TERMINATE = "terminate"
FINAL = "final"

KNOWN_TYPES = frozenset({INIT, READY, RESET, RESET_ACK, STEP, RESULT, ACTION, TERMINATE, FINAL})


@dataclass
class Message:
    type: str
    session_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    seq: Optional[int] = None  # 可选：数据帧序号（Loader 推流递增，排查丢帧用）

    def __post_init__(self) -> None:
        if self.type not in KNOWN_TYPES:
            raise ValueError(f"未知消息类型: {self.type!r}")
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ValueError(f"非法 session_id: {self.session_id!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "session_id": self.session_id, "payload": self.payload, "seq": self.seq}

    @classmethod
    def from_dict(cls, data) -> "Message":
        if not isinstance(data, dict):
            raise ValueError(f"消息必须是 dict，收到 {type(data).__name__}")
        return cls(
            type=data.get("type", ""),
            session_id=data.get("session_id", ""),
            payload=data.get("payload", {}),
            seq=data.get("seq"),
        )


# ---- 控制消息构造 ----


def init(session_id: str, config: Optional[dict[str, Any]] = None,
         body_profile: Optional[dict[str, Any]] = None) -> Message:
    payload = dict(config) if config else {}
    if body_profile:
        payload["body_profile"] = body_profile
    return Message(INIT, session_id, payload)


def ready(session_id: str, capabilities: Optional[dict[str, Any]] = None) -> Message:
    return Message(READY, session_id, capabilities or {})


def reset(session_id: str, testcase_meta: Optional[dict[str, Any]] = None) -> Message:
    return Message(RESET, session_id, testcase_meta or {})


def reset_ack(session_id: str, ok: bool = True) -> Message:
    return Message(RESET_ACK, session_id, {"ok": bool(ok)})


def terminate(session_id: str, reason: str) -> Message:
    return Message(TERMINATE, session_id, {"reason": reason})


def final(session_id: str, self_stats: Optional[dict[str, Any]] = None) -> Message:
    return Message(FINAL, session_id, self_stats or {})


# ---- 数据消息构造/解析（载荷形态按 v1.1 §4.2/§5.2 冻结） ----


@dataclass
class Result:
    """SUT 开环回复：payload = {module, data}（data 为 §4.2 数据面信封）。"""

    module: str
    data: dict


@dataclass
class Action:
    """SUT 闭环回复：payload = {module, data}（data 为 §4.2 数据面信封）。"""

    module: str
    data: dict


def step(session_id: str, observation: Optional[dict], done: bool) -> Message:
    """observation: 外层信封 dict（{timestamp, module, data}，见 schema.make_observation）或 None。"""
    return Message(
        STEP,
        session_id,
        {
            "done": bool(done),
            "observation": observation,
        },
    )


def parse_step(message: Message) -> tuple[Optional[dict], bool]:
    """返回 observation 外层信封 dict（data 字段需 schema.decode_observation 解码）。"""
    if message.type != STEP:
        raise ValueError(f"期望 STEP 消息，收到 {message.type!r}")
    obs = message.payload.get("observation")
    return obs, bool(message.payload.get("done"))


def result(session_id: str, output: Result | dict) -> Message:
    """output: Result reply 或 {module, data} payload dict。"""
    payload = {"module": output.module, "data": output.data} if isinstance(output, Result) else dict(output)
    return Message(RESULT, session_id, payload)


def parse_result(message: Message) -> dict:
    """返回 result payload dict（{module, data}；data 需 schema.decode_result 解码）。"""
    if message.type != RESULT:
        raise ValueError(f"期望 RESULT 消息，收到 {message.type!r}")
    return dict(message.payload)


def action(session_id: str, command: Action | dict) -> Message:
    """command: Action reply 或 {module, data} payload dict。"""
    payload = {"module": command.module, "data": command.data} if isinstance(command, Action) else dict(command)
    return Message(ACTION, session_id, payload)


def parse_action(message: Message) -> dict:
    """返回 action payload dict（{module, data}；data 需 schema.decode_action 解码）。"""
    if message.type != ACTION:
        raise ValueError(f"期望 ACTION 消息，收到 {message.type!r}")
    return dict(message.payload)
