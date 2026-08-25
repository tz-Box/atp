"""插件注册表：Checker / Dataset / Body 三件套注册（v1.1 §7 冻结）。

注册键必须带命名空间（如 pipe.slam.ape / pipe.slam.rosbag），禁止裸名。
场景装配时校验 produces ⊇ consumes，fail fast。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable, Optional


class RegistryError(Exception):
    """插件注册或查找失败。"""


_CHECKERS: dict[str, type] = {}
_DATASETS: dict[str, type] = {}
_BODIES: dict[str, type] = {}


def _validate_namespace(key: str, kind: str) -> str:
    """校验注册键是否带命名空间，返回命名空间前缀。"""
    if "." not in key:
        raise RegistryError(
            f"{kind} 注册键 {key!r} 缺少命名空间，格式应为 '<命名空间>.<名称>'"
        )
    return key.rsplit(".", 1)[0]


def _register(kind: str, store: dict[str, type], key: str, cls: type) -> None:
    _validate_namespace(key, kind)
    if key in store:
        raise RegistryError(f"{kind} 键冲突：{key!r} 已注册")
    store[key] = cls


def register_checker(key: str, cls: type) -> None:
    """注册 Checker，键格式 '<命名空间>.<名称>'（如 pipe.slam.ape）。"""
    _register("Checker", _CHECKERS, key, cls)


def register_dataset(key: str, cls: type) -> None:
    """注册 Dataset，键格式 '<命名空间>.<名称>'（如 pipe.slam.rosbag）。"""
    _register("Dataset", _DATASETS, key, cls)


def register_body(key: str, cls: type) -> None:
    """注册 Body，键格式 '<名称>'（如 pbox_v1，暂允许不带命名空间）。"""
    if key in _BODIES:
        raise RegistryError(f"Body 键冲突：{key!r} 已注册")
    _BODIES[key] = cls


def get_checker(key: str) -> type:
    try:
        return _CHECKERS[key]
    except KeyError:
        raise RegistryError(
            f"未注册的 Checker: {key!r}，可用: {sorted(_CHECKERS)}"
        )


def get_dataset(key: str) -> type:
    try:
        return _DATASETS[key]
    except KeyError:
        raise RegistryError(
            f"未注册的 Dataset: {key!r}，可用: {sorted(_DATASETS)}"
        )


def get_body(key: str) -> type:
    try:
        return _BODIES[key]
    except KeyError:
        raise RegistryError(
            f"未注册的 Body: {key!r}，可用: {sorted(_BODIES)}"
        )


def list_plugins() -> dict[str, list[str]]:
    """列出已注册插件，用于调试。"""
    return {
        "checkers": sorted(_CHECKERS),
        "datasets": sorted(_DATASETS),
        "bodies": sorted(_BODIES),
    }


def available_plugins() -> list[str]:
    """仓内可用插件命名空间（扫描 plugins/ 目录，未加载也可见）。

    插件按需 load_plugin 才注册，服务启动时注册表为空；capabilities 自报（M-E9a）
    需要的是"本 ATP 能跑什么"的静态事实，故直接扫目录。
    """
    if not _PLUGIN_ROOT.is_dir():
        return []
    return sorted(p.name for p in _PLUGIN_ROOT.iterdir()
                  if p.is_dir() and (p / "__init__.py").is_file())


def available_bodies() -> list[str]:
    """仓内可用本体台架（body/*.yaml 的 stem，即 scenario.yaml `body:` 引用键）。"""
    body_root = _PLUGIN_ROOT.parent / "body"
    if not body_root.is_dir():
        return []
    return sorted(p.stem for p in body_root.glob("*.yaml"))


def validate_produces_consumes(
    checker_consumes: list[str],
    dataset_produces: list[str],
) -> None:
    """校验场景装配时 checker 声明的数据需求是否被 dataset 满足。

    checker_consumes 中的每个 schema 必须在 dataset_produces 中。
    """
    missing = [s for s in checker_consumes if s not in dataset_produces]
    if missing:
        raise RegistryError(
            f"Checker 声明 consumes {missing} 但 Dataset produces "
            f"{dataset_produces} 不包含，场景装配失败"
        )


# ---------------------------------------------------------------- 插件加载

# 仓库根目录（src/autotest/registry.py → parents[2]）；plugins/ 在仓库根下
_PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugins"


def load_plugin(namespace: str):
    """按命名空间加载插件包（plugins/<namespace>/__init__.py），触发三件套注册。

    插件目录名含 '.'（如 pipe.slam），不能走正常包导入，按文件路径加载；
    重复加载幂等（sys.modules 查重）。返回插件模块对象，便于测试/示例直接取符号：
        slam = load_plugin("pipe.slam"); slam.SyntheticSlamDataset
    """
    name = f"plugins.{namespace}"
    if name in sys.modules:
        return sys.modules[name]
    init = _PLUGIN_ROOT / namespace / "__init__.py"
    if not init.is_file():
        raise RegistryError(f"插件不存在: {init}（命名空间 {namespace!r}）")
    spec = importlib.util.spec_from_file_location(
        name, init, submodule_search_locations=[str(init.parent)]
    )
    if spec is None or spec.loader is None:
        raise RegistryError(f"插件加载失败: {init}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
