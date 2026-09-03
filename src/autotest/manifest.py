"""算法侧静态注册声明：算法仓库根目录的 scenario.yaml。

v1.1 §3 冻结：module 常量化移除，改由 consumes 列表声明算法消费的数据 schema。
批次 F（M-F2，R2）：新增 scenarios 场景清单 + runtime 声明，Schema 权威见 docs/07-附录-scenario-yaml-schema.md。
旧字段 scenario: 单引用 = scenarios 省略时的默认匿名场景（向后兼容）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import yaml

_SCENARIO_ID = re.compile(r"^[a-z0-9_]+$")
_RUNTIME_TYPES = ("host", "venv", "docker")


class ScenarioUnknownError(ValueError):
    """submit.scenario 引用的 id 不在清单中（4xx scenario_unknown 语义）。"""

    def __init__(self, requested: str, available: list[str]):
        self.requested = requested
        self.available = available
        super().__init__(f"未知 scenario: {requested}（可用: {', '.join(available) or '（无清单）'}）")


@dataclass
class ScenarioEntry:
    """场景清单项（docs/07-附录-scenario-yaml-schema.md §3）：id + 场景文件引用 + 深合并覆盖键。"""
    id: str                       # ^[a-z0-9_]+$；匿名默认场景为 "default"
    scenario: str                 # 场景文件（相对 manifest 目录）
    description: str = ""
    hyperparams: dict = field(default_factory=dict)     # 深合并覆盖场景文件同名字段
    checker_config: dict = field(default_factory=dict)
    dataset_config: dict = field(default_factory=dict)
    baseline: str = ""            # 仓内参考基线（相对仓根，可选）


@dataclass
class AlgorithmManifest:
    launch: str  # 启动命令（在算法根目录执行）
    consumes: list[str] = field(default_factory=list)  # 算法声明的输入 schema（命名空间键）
    scenario: str = ""  # 建议场景（评测方可覆盖）；scenarios 省略时的唯一匿名场景
    scenarios: list[ScenarioEntry] = field(default_factory=list)  # 场景清单（M-F2）
    runtime: dict = field(default_factory=dict)  # 运行环境声明（M-F4/F5；缺省 host）
    required_sensors: dict = field(default_factory=dict)  # {类型: [实例名...]}
    output_topic: str = ""  # 算法自有产物 topic（ROS 侧，可选）
    hyperparams: dict = field(default_factory=dict)  # 算法超参，经 INIT 下发
    image: str = ""  # 可选：docker 镜像名，填了则以 docker+bind 方式拉起
    dir: str = ""  # 算法根目录（yaml 所在目录），解析时填入


def _parse_scenarios(data: list, manifest_name: str) -> list[ScenarioEntry]:
    """解析并校验 scenarios 清单；非法 → ValueError（上层映射 manifest_invalid）。"""
    entries: list[ScenarioEntry] = []
    seen: set[str] = set()
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"scenarios[{i}] 需为对象: {manifest_name}")
        sid = item.get("id")
        if not sid or not isinstance(sid, str) or not _SCENARIO_ID.match(sid):
            raise ValueError(f"scenarios[{i}].id 缺失或非法（^[a-z0-9_]+$）: {manifest_name}")
        if sid in seen:
            raise ValueError(f"scenarios id 重复: {sid}（{manifest_name}）")
        seen.add(sid)
        if not item.get("scenario"):
            raise ValueError(f"scenarios[{i}].scenario 缺失（场景文件引用）: {manifest_name}")
        entries.append(ScenarioEntry(
            id=sid,
            scenario=item["scenario"],
            description=item.get("description", ""),
            hyperparams=item.get("hyperparams") or {},
            checker_config=item.get("checker_config") or {},
            dataset_config=item.get("dataset_config") or {},
            baseline=item.get("baseline", ""),
        ))
    return entries


def load_algorithm_manifest(path: str) -> AlgorithmManifest:
    """读取算法 scenario.yaml，dir 为 yaml 所在目录（launch 的工作目录）。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"算法 manifest 不存在: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "launch" not in data:
        raise ValueError(f"manifest 非法（需含 launch 的对象）: {p}")
    raw_scenarios = data.get("scenarios") or []
    if not isinstance(raw_scenarios, list):
        raise ValueError(f"scenarios 需为列表: {p}")
    entries = _parse_scenarios(raw_scenarios, str(p))
    if not entries and data.get("scenario"):
        # 向后兼容：旧式单场景引用 = 匿名默认场景
        entries = [ScenarioEntry(id="default", scenario=data["scenario"])]
    runtime = data.get("runtime") or {}
    if not isinstance(runtime, dict) or runtime.get("type", "host") not in _RUNTIME_TYPES:
        raise ValueError(f"runtime.type 未知（host|venv|docker）: {p}")
    manifest = AlgorithmManifest(
        launch=data["launch"],
        consumes=data.get("consumes", []),
        scenario=data.get("scenario", ""),
        scenarios=entries,
        runtime=runtime,
        required_sensors=data.get("required_sensors", {}),
        output_topic=data.get("output_topic", ""),
        hyperparams=data.get("hyperparams", {}),
        image=data.get("image", ""),
        dir=str(p.parent),
    )
    return manifest


def select_scenarios(manifest: AlgorithmManifest,
                     requested: Union[None, str, list[str]]) -> list[ScenarioEntry]:
    """按 submit.scenario 选择场景（docs/07-附录-scenario-yaml-schema.md §7）。

    None → 清单全部；str/list → 按 id 过滤（保清单顺序）；未中 → ScenarioUnknownError。
    """
    if requested is None:
        return list(manifest.scenarios)
    ids = [requested] if isinstance(requested, str) else list(requested)
    by_id = {e.id: e for e in manifest.scenarios}
    for sid in ids:
        if sid not in by_id:
            raise ScenarioUnknownError(sid, [e.id for e in manifest.scenarios])
    return [e for e in manifest.scenarios if e.id in set(ids)]


def is_scenario_path(value: object) -> bool:
    """submit.scenario 值形态分派：路径值（含 / 或以 .yaml 结尾）→ 旧语义（manifest 相对路径）。"""
    return isinstance(value, str) and ("/" in value or value.endswith(".yaml"))


def deep_merge(base: dict, override: dict) -> dict:
    """递归深合并（清单项覆盖键语义，docs/07-附录-scenario-yaml-schema.md §3）：override 逐键覆盖 base。"""
    out = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out
