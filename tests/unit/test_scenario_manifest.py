"""场景清单与 runtime 声明单测（M-F1~M-F3，Schema 权威 docs/03-scenario-schema.md）。

覆盖：
- scenarios 清单解析与校验（id 形态/重复/缺字段/非列表）；
- 旧式 scenario 单引用 → default 匿名场景（向后兼容）；
- runtime.type 校验（host/venv/docker；未知 → manifest_invalid 语义）；
- select_scenarios 三态（None 全跑 / str / list 保清单顺序 / 未知 → ScenarioUnknownError）；
- is_scenario_path 形态分派（路径值 vs 场景 id）；
- deep_merge 递归覆盖语义（清单项覆盖键）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autotest.manifest import (ScenarioUnknownError, deep_merge, is_scenario_path,
                               load_algorithm_manifest, select_scenarios)


def _write_manifest(tmp_path: Path, data: dict) -> str:
    p = tmp_path / "scenario.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(p)


def _two_entry_manifest(tmp_path: Path):
    return load_algorithm_manifest(_write_manifest(tmp_path, {
        "launch": "echo ok",
        "scenarios": [
            {"id": "fast", "scenario": "scen/a.yaml", "description": "快速",
             "dataset_config": {"n_testcases": 1}},
            {"id": "full", "scenario": "scen/b.yaml", "baseline": "baseline/full.json"},
        ],
    }))


# ---- 清单解析 ----

def test_scenarios_list_parsed(tmp_path):
    m = _two_entry_manifest(tmp_path)
    assert [e.id for e in m.scenarios] == ["fast", "full"]
    fast, full = m.scenarios
    assert fast.scenario == "scen/a.yaml"
    assert fast.description == "快速"
    assert fast.dataset_config == {"n_testcases": 1}
    assert fast.hyperparams == {} and fast.checker_config == {}
    assert full.baseline == "baseline/full.json"


def test_legacy_scenario_becomes_default_entry(tmp_path):
    m = load_algorithm_manifest(_write_manifest(tmp_path, {
        "launch": "echo ok", "scenario": "legacy.yaml"}))
    assert len(m.scenarios) == 1
    assert m.scenarios[0].id == "default"
    assert m.scenarios[0].scenario == "legacy.yaml"


def test_no_scenarios_no_scenario_gives_empty(tmp_path):
    m = load_algorithm_manifest(_write_manifest(tmp_path, {"launch": "echo ok"}))
    assert m.scenarios == []


def test_scenarios_invalid_id_rejected(tmp_path):
    with pytest.raises(ValueError, match="id 缺失或非法"):
        load_algorithm_manifest(_write_manifest(tmp_path, {
            "launch": "echo ok",
            "scenarios": [{"id": "Fast-1", "scenario": "a.yaml"}],
        }))


def test_scenarios_duplicate_id_rejected(tmp_path):
    with pytest.raises(ValueError, match="id 重复"):
        load_algorithm_manifest(_write_manifest(tmp_path, {
            "launch": "echo ok",
            "scenarios": [
                {"id": "fast", "scenario": "a.yaml"},
                {"id": "fast", "scenario": "b.yaml"},
            ],
        }))


def test_scenarios_missing_scenario_field_rejected(tmp_path):
    with pytest.raises(ValueError, match="scenario 缺失"):
        load_algorithm_manifest(_write_manifest(tmp_path, {
            "launch": "echo ok", "scenarios": [{"id": "fast"}]}))


def test_scenarios_not_a_list_rejected(tmp_path):
    with pytest.raises(ValueError, match="scenarios 需为列表"):
        load_algorithm_manifest(_write_manifest(tmp_path, {
            "launch": "echo ok", "scenarios": {"fast": "a.yaml"}}))


# ---- runtime 声明（M-F4/F5 前置校验） ----

def test_runtime_defaults_empty(tmp_path):
    m = load_algorithm_manifest(_write_manifest(tmp_path, {"launch": "echo ok"}))
    assert m.runtime == {}


@pytest.mark.parametrize("rtype", ["host", "venv", "docker"])
def test_runtime_type_accepted(tmp_path, rtype):
    m = load_algorithm_manifest(_write_manifest(tmp_path, {
        "launch": "echo ok", "runtime": {"type": rtype}}))
    assert m.runtime["type"] == rtype


def test_runtime_type_unknown_rejected(tmp_path):
    with pytest.raises(ValueError, match="runtime.type 未知"):
        load_algorithm_manifest(_write_manifest(tmp_path, {
            "launch": "echo ok", "runtime": {"type": "k8s"}}))


# ---- select_scenarios ----

def test_select_none_returns_all(tmp_path):
    m = _two_entry_manifest(tmp_path)
    assert [e.id for e in select_scenarios(m, None)] == ["fast", "full"]


def test_select_str_single(tmp_path):
    m = _two_entry_manifest(tmp_path)
    assert [e.id for e in select_scenarios(m, "full")] == ["full"]


def test_select_list_keeps_manifest_order(tmp_path):
    m = _two_entry_manifest(tmp_path)
    # 请求顺序 [full, fast] → 返回按清单顺序 [fast, full]（执行顺序确定性）
    assert [e.id for e in select_scenarios(m, ["full", "fast"])] == ["fast", "full"]


def test_select_unknown_raises(tmp_path):
    m = _two_entry_manifest(tmp_path)
    with pytest.raises(ScenarioUnknownError) as exc_info:
        select_scenarios(m, ["fast", "nope"])
    err = exc_info.value
    assert err.requested == "nope"
    assert err.available == ["fast", "full"]
    assert "nope" in str(err)


# ---- is_scenario_path 形态分派 ----

@pytest.mark.parametrize("value,expected", [
    ("scenarios/a.yaml", True),   # 含 / → 路径
    ("a.yaml", True),             # .yaml 结尾 → 路径
    ("default", False),           # 纯 id
    ("fast", False),
    (None, False),
    (["fast", "full"], False),    # id 列表
])
def test_is_scenario_path_dispatch(value, expected):
    assert is_scenario_path(value) is expected


# ---- deep_merge ----

def test_deep_merge_nested_override():
    base = {"a": 1, "nested": {"x": 1, "y": 2}, "keep": "k"}
    out = deep_merge(base, {"nested": {"y": 20, "z": 30}, "b": 2})
    assert out == {"a": 1, "nested": {"x": 1, "y": 20, "z": 30}, "keep": "k", "b": 2}
    assert base == {"a": 1, "nested": {"x": 1, "y": 2}, "keep": "k"}  # base 不被修改


def test_deep_merge_scalar_replaces_dict():
    # override 为非 dict → 整体替换（不递归）
    assert deep_merge({"a": {"x": 1}}, {"a": 5}) == {"a": 5}


def test_deep_merge_none_safe():
    assert deep_merge(None, {"a": 1}) == {"a": 1}
    assert deep_merge({"a": 1}, None) == {"a": 1}
