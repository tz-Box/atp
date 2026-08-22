"""data 信封协议：跨进程数据传输的统一封装（v1.1 §4.2 冻结）。

数据面信封（observation/action/result 的 data 字段）：{schema, v, enc, blob}
- schema: 命名空间.类型名（如 pipe.slam.SlamObs）
- v: schema 版本（整数）
- enc: 编码（msgpack / pb）
- blob: 编码后的字节流

observation 外层（v1.1 §4.2 冻结）：{timestamp, module, data}
- timestamp: 唯一权威时间（§5.4，算法禁读墙上时钟）
- module: 插件命名空间（如 pipe.slam）
- data: 数据面信封

GT（v1.1 §6 冻结）：{schema, v, data}——进程内传递、不编码，
由产出它的 World 声明、由消费它的 checker 解析，核心不感知内容。

未知 schema 必须拒收并记 error（不再静默透传）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import msgpack


class SchemaError(Exception):
    """schema 解析或编码失败。"""


# ---------------------------------------------------------------- 注册表

_DECODERS: dict[tuple[str, str], Callable] = {}  # (kind, schema_id) -> decoder
_ENCODERS: dict[tuple[str, str], Callable] = {}  # (kind, schema_id) -> encoder


def register_data(kind: str, schema_id: str, decoder: Callable, encoder: Optional[Callable] = None) -> None:
    """注册 schema 的编解码器。

    kind: observation / result / action
    schema_id: 命名空间.类型名（如 pipe.slam.SlamObs）
    decoder: blob -> 对象
    encoder: 对象 -> blob（可选，缺省用 msgpack.packb）
    """
    key = (kind, schema_id)
    if key in _DECODERS:
        raise SchemaError(f"data schema 冲突：{kind}/{schema_id} 已注册")
    _DECODERS[key] = decoder
    _ENCODERS[key] = encoder or (lambda obj: msgpack.packb(obj.to_dict(), use_bin_type=True))


def _decode_envelope(kind: str, envelope: dict) -> Any:
    """解码 data 信封，未知 schema 拒收。"""
    schema_id = envelope.get("schema")
    if not schema_id:
        raise SchemaError(f"{kind} 信封缺少 schema 字段")
    key = (kind, schema_id)
    decoder = _DECODERS.get(key)
    if decoder is None:
        raise SchemaError(
            f"未注册的 {kind} schema: {schema_id!r}，可用: {sorted(k for k, s in _DECODERS if k == kind)}"
        )
    blob = envelope.get("blob")
    if blob is None:
        raise SchemaError(f"{kind} 信封缺少 blob 字段")
    return decoder(blob)


def _encode_envelope(kind: str, schema_id: str, obj: Any) -> dict:
    """编码对象为 data 信封。"""
    key = (kind, schema_id)
    encoder = _ENCODERS.get(key)
    if encoder is None:
        raise SchemaError(f"未注册的 {kind} schema: {schema_id!r}")
    return {
        "schema": schema_id,
        "v": 1,
        "enc": "msgpack",
        "blob": encoder(obj),
    }


def decode_observation(envelope: dict) -> Any:
    """解码观测信封。"""
    return _decode_envelope("observation", envelope)


def decode_result(envelope: dict) -> Any:
    """解码结果信封。"""
    return _decode_envelope("result", envelope)


def decode_action(envelope: dict) -> Any:
    """解码闭环指令信封。"""
    return _decode_envelope("action", envelope)


def encode_observation(schema_id: str, obj: Any) -> dict:
    """编码观测为信封。"""
    return _encode_envelope("observation", schema_id, obj)


def encode_result(schema_id: str, obj: Any) -> dict:
    """编码结果为信封。"""
    return _encode_envelope("result", schema_id, obj)


def encode_action(schema_id: str, obj: Any) -> dict:
    """编码闭环指令为信封。"""
    return _encode_envelope("action", schema_id, obj)


# ---------------------------------------------------------------- observation 外层（v1.1 §4.2 冻结）


def make_observation(module: str, timestamp: float, data: dict) -> dict:
    """组 observation 外层信封：{timestamp, module, data}。

    module: 插件命名空间；timestamp: 唯一权威时间（§5.4）；data: 数据面信封。
    """
    return {"timestamp": float(timestamp), "module": module, "data": data}


# ---------------------------------------------------------------- GT（v1.1 §6 冻结：{schema, v, data}，进程内不编码）


def encode_ground_truth(schema_id: str, data: dict, v: int = 1) -> dict:
    """组 GT 信封：{schema, v, data}。GT 进程内传递，不经 enc/blob 编码。"""
    return {"schema": schema_id, "v": int(v), "data": data}


def decode_ground_truth(envelope: dict) -> Any:
    """取 GT 信封的 data（由消费它的 checker 解析内容）。"""
    if "schema" not in envelope or "data" not in envelope:
        raise SchemaError(f"非法 GT 信封（缺 schema/data 字段）: {sorted(envelope)}")
    return envelope["data"]


# ---------------------------------------------------------------- 基础消息类型（核心保留，不含算法）

@dataclass
class Pose:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    qx: float = 0.0
    qy: float = 0.0
    qz: float = 0.0
    qw: float = 1.0

    def to_list(self) -> list[float]:
        return [self.x, self.y, self.z, self.qx, self.qy, self.qz, self.qw]

    @classmethod
    def from_list(cls, data: list[float]) -> "Pose":
        return cls(*data[:7])


@dataclass
class Imu:
    angular_velocity: tuple[float, float, float] = (0.0, 0.0, 0.0)
    linear_acceleration: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def to_dict(self) -> dict:
        return {
            "angular_velocity": list(self.angular_velocity),
            "linear_acceleration": list(self.linear_acceleration),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Imu":
        return cls(
            angular_velocity=tuple(data["angular_velocity"]),
            linear_acceleration=tuple(data["linear_acceleration"]),
        )


@dataclass
class StampedPose:
    timestamp: float = 0.0
    pose: Pose = field(default_factory=Pose)

    def to_dict(self) -> dict:
        return {"timestamp": self.timestamp, "pose": self.pose.to_list()}

    @classmethod
    def from_dict(cls, data: dict) -> "StampedPose":
        return cls(timestamp=float(data["timestamp"]), pose=Pose.from_list(data["pose"]))
