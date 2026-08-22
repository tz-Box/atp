"""Body 资产注册与本体一致性校验。

本体 = 机器人型号 + URDF + 标定 + 传感器布置 + 外参
v1.1 §8 冻结：所有数据集必须声明 body_id + body_version，确保数据集与本体对齐。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml


class BodyError(Exception):
    """本体资产加载或校验失败。"""


class Body:
    """本体资产描述（从 YAML 加载）。"""

    def __init__(self, data: dict) -> None:
        body = data.get("body")
        if not isinstance(body, dict):
            raise BodyError("body.yaml 缺少 body 键")
        self.name: str = body.get("name", "")
        self.version: str = str(body.get("version", ""))
        self.urdf_path: str = body.get("urdf_path", "")
        self.sensors: dict = body.get("sensors", {})
        self.ground_clearance: float = float(body.get("ground_clearance", 0.0))
        self.kinematics: dict = body.get("kinematics", {})

    @property
    def sensor_topics(self) -> dict[str, dict[str, str]]:
        """{类型: {实例名: topic}} 供 INIT 下发。"""
        return {
            stype: {name: info["topic"] for name, info in instances.items()}
            for stype, instances in self.sensors.items()
        }

    def validate_against(self, dataset_body_id: str, dataset_body_version: str) -> None:
        """校验数据集声明的 body 与当前加载的 body 是否一致。"""
        if dataset_body_id != self.name:
            raise BodyError(
                f"数据集声明 body_id={dataset_body_id!r}，与加载的本体 {self.name!r} 不一致"
            )
        if str(dataset_body_version) != self.version:
            raise BodyError(
                f"数据集声明 body_version={dataset_body_version!r}，"
                f"与加载的本体版本 {self.version!r} 不一致"
            )


def load_body(path: str | Path) -> Body:
    """从 YAML 文件加载本体资产。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Body(data)


def sensor_config_from_body(body: Body) -> dict[str, dict[str, str]]:
    """从 body 生成 INIT 下发的 sensor_config（{类型: {实例名: topic}}）。"""
    return body.sensor_topics
