"""插件注册表：checker / 数据源（world 工厂）按名称注册，供场景配置按名加载。

数据源工厂统一返回 IWorld 实例（rosbag/rostopic/device 对算法同接口）。
"""
from __future__ import annotations

from typing import Any, Callable

_CHECKERS: dict[str, Any] = {}
_WORLDS: dict[str, Callable] = {}


def register_checker(name: str, checker_cls: Any) -> None:
    _CHECKERS[name] = checker_cls


def get_checker(name: str) -> Any:
    if name not in _CHECKERS:
        raise KeyError(f"未注册的 checker: {name!r}（已注册: {sorted(_CHECKERS)}）")
    return _CHECKERS[name]


def register_dataset(name: str, factory: Callable) -> None:
    """注册数据源工厂：接收场景 dataset.config（**kwargs），返回 IWorld 实例。"""
    _WORLDS[name] = factory


def get_dataset(name: str) -> Callable:
    if name not in _WORLDS:
        raise KeyError(f"未注册的数据源: {name!r}（已注册: {sorted(_WORLDS)}）")
    return _WORLDS[name]
