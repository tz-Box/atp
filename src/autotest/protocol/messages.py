"""协议消息信封与消息类型：协议单一来源。

所有跨进程消息都打包为 Message（可 msgpack 序列化）；传输层只负责收发 dict，不感知语义。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .schema import Action, Observation, Result

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


def init(session_id: str, config: Optional[dict[str, Any]] = None) -> Message:
    return Message(INIT, session_id, config or {})


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


# ---- 数据消息构造/解析（含 schema 编码，协议单一来源） ----


def step(session_id: str, observation: Optional[Observation], done: bool) -> Message:
    return Message(
        STEP,
        session_id,
        {
            "done": bool(done),
            "observation": observation.to_dict() if observation is not None else None,
        },
    )


def parse_step(message: Message) -> tuple[Optional[Observation], bool]:
    if message.type != STEP:
        raise ValueError(f"期望 STEP 消息，收到 {message.type!r}")
    obs_data = message.payload.get("observation")
    obs = Observation.from_dict(obs_data) if obs_data else None
    return obs, bool(message.payload.get("done"))


def result(session_id: str, output: Result) -> Message:
    return Message(RESULT, session_id, output.to_dict())


def parse_result(message: Message) -> Result:
    if message.type != RESULT:
        raise ValueError(f"期望 RESULT 消息，收到 {message.type!r}")
    return Result.from_dict(message.payload)


def action(session_id: str, command: Action) -> Message:
    return Message(ACTION, session_id, command.to_dict())


def parse_action(message: Message) -> Action:
    if message.type != ACTION:
        raise ValueError(f"期望 ACTION 消息，收到 {message.type!r}")
    return Action.from_dict(message.payload)
