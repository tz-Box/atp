"""测试内容：回归报告 compare / render / 基线存取。

vs_baseline 方向修复（M 2026-09-11 按缺陷批准）的判定纪律在此固化：
- 好/坏只来自被测仓声明的方向（results 行 `directions`），缺声明不猜；
- `same` 只表示「确实没变化」；「缺方向」「无可比指标」「有好有坏」各自成态——
  修复前四件事挤在一个 `same` 里，对 ctrl.invp 一族（5 指标 3 个越大越好）
  真劣化/真改善全落 same，唯一出声（improved）的时候是反的。
"""
from __future__ import annotations

from autotest.server.report import compare, load_baseline, render_markdown, save_baseline

_INVP_DIRECTIONS = {"survived": "higher", "survival_time": "higher",
                    "upright_ratio": "higher", "max_abs_theta": "lower",
                    "settle_error": "lower"}


def _report(results: list[dict]) -> dict:
    return {"job_id": "job-1", "results": results}


def _tc(metrics: dict, passed: bool | None = True, directions: dict | None = None,
        tc: str = "tc0") -> dict:
    row = {"testcase_id": tc, "passed": passed, "metrics": metrics}
    if directions:
        row["directions"] = directions
    return row


def test_compare_improved_with_declared_direction() -> None:
    baseline = _report([_tc({"ate_rmse": 0.10})])
    current = _report([_tc({"ate_rmse": 0.05}, directions={"ate_rmse": "lower"})])
    rows = compare(baseline, current)
    assert rows[0]["change"] == "improved"
    assert rows[0]["deltas"]["ate_rmse"] == -0.05
    assert rows[0]["metric_judgements"] == {"ate_rmse": "better"}


def test_compare_worse_with_declared_direction() -> None:
    baseline = _report([_tc({"ate_rmse": 0.05})])
    current = _report([_tc({"ate_rmse": 0.12}, directions={"ate_rmse": "lower"})])
    rows = compare(baseline, current)
    assert rows[0]["change"] == "worse"
    assert rows[0]["deltas"]["ate_rmse"] == 0.07


def test_compare_higher_is_better_not_reversed() -> None:
    """★缺陷原型：越大越好的指标劣化，修复前被报成 improved（方向写死「越小越好」）。"""
    baseline = _report([_tc({"upright_ratio": 0.95})])
    current = _report([_tc({"upright_ratio": 0.55},
                           directions={"upright_ratio": "higher"})])
    rows = compare(baseline, current)
    assert rows[0]["change"] == "worse"
    assert rows[0]["metric_judgements"] == {"upright_ratio": "worse"}
    # 反向：涨了就是变好
    rows = compare(current, _report([_tc({"upright_ratio": 0.95},
                                         directions={"upright_ratio": "higher"})]))
    assert rows[0]["change"] == "improved"


def test_compare_mixed_signs_resolve_with_directions() -> None:
    """★缺陷原型：大好降+小好升（典型真劣化）修复前落 same——完全不可见。

    有了方向声明，符号混合规约成一致的坏；真改善同理规约成一致的好。
    """
    baseline = _report([_tc({"upright_ratio": 0.95, "settle_error": 0.010,
                             "max_abs_theta": 0.05})])
    degraded = _report([_tc({"upright_ratio": 0.55, "settle_error": 0.018,
                             "max_abs_theta": 0.19}, directions=_INVP_DIRECTIONS)])
    assert compare(baseline, degraded)[0]["change"] == "worse"
    better = _report([_tc({"upright_ratio": 0.99, "settle_error": 0.006,
                           "max_abs_theta": 0.02}, directions=_INVP_DIRECTIONS)])
    assert compare(baseline, better)[0]["change"] == "improved"


def test_compare_undeclared_direction_is_undetermined_not_guessed() -> None:
    """缺方向声明的指标变了 → undetermined，不猜、不上色（M：缺省不得猜）。"""
    baseline = _report([_tc({"ate_rmse": 0.10})])
    current = _report([_tc({"ate_rmse": 0.05})])   # 无 directions —— 修复前会报 improved
    rows = compare(baseline, current)
    assert rows[0]["change"] == "undetermined"
    assert rows[0]["metric_judgements"] == {"ate_rmse": "undetermined"}
    assert rows[0]["deltas"]["ate_rmse"] == -0.05  # 事实层永远给


def test_compare_partial_declaration() -> None:
    """只声明了部分指标：已声明的同向变好，但未声明的也在动 → 整行仍 undetermined；
    已声明的自己就有好有坏 → mixed（确凿的有涨有跌，未知项不影响）。"""
    baseline = _report([_tc({"survived": 0.9, "foo": 1.0})])
    part = {"survived": "higher"}
    current = _report([_tc({"survived": 0.95, "foo": 1.3}, directions=part)])
    assert compare(baseline, current)[0]["change"] == "undetermined"
    mixed = _report([_tc({"survived": 0.5, "settle_error": 0.5, "foo": 1.3},
                         directions={"survived": "higher", "settle_error": "lower"})])
    base2 = _report([_tc({"survived": 0.9, "settle_error": 0.9, "foo": 1.0})])
    assert compare(base2, mixed)[0]["change"] == "mixed"


def test_compare_same_only_when_truly_unchanged() -> None:
    """same 只表示逐指标数值确实没变（事实，无需声明）；有未声明指标在动就不是 same。"""
    baseline = _report([_tc({"ate_rmse": 0.10, "foo": 1.0})])
    current = _report([_tc({"ate_rmse": 0.10, "foo": 1.0})])
    assert compare(baseline, current)[0]["change"] == "same"


def test_compare_no_comparable_is_its_own_state() -> None:
    """无可比指标独立成态，不并进 same——「不认识」和「没变化」不是一个值。"""
    baseline = _report([_tc(None, passed=None)])
    current = _report([_tc(None, passed=None)])
    assert compare(baseline, current)[0]["change"] == "no_comparable"


def test_compare_passed_flip_takes_priority() -> None:
    baseline = _report([_tc({"ate_rmse": 0.05}, passed=False)])
    current = _report([_tc({"ate_rmse": 0.05}, passed=True)])
    assert compare(baseline, current)[0]["change"] == "improved"
    assert compare(current, baseline)[0]["change"] == "regressed"


def test_compare_new_testcase() -> None:
    baseline = _report([_tc({}, tc="tc0")])
    current = _report([_tc({}, tc="tc1")])
    rows = compare(baseline, current)
    assert rows[0]["change"] == "new"
    assert "baseline" not in rows[0]


def test_render_markdown() -> None:
    rows = compare(
        _report([_tc({"ate_rmse": 0.10})]),
        _report([_tc({"ate_rmse": 0.05}, directions={"ate_rmse": "lower"})]),
    )
    md = render_markdown(_report([_tc({"ate_rmse": 0.05})]), rows)
    assert "tc0" in md and "improved" in md
    assert "ate_rmse=-0.0500" in md


def test_save_and_load_baseline(tmp_path) -> None:
    report_dir = tmp_path / "job-1"
    report_dir.mkdir()
    (report_dir / "report.json").write_text(
        '{"job_id": "job-1", "results": [{"testcase_id": "tc0", "passed": true}]}',
        encoding="utf-8",
    )
    target = save_baseline(report_dir, baseline_path=tmp_path / "baseline.json")
    assert target.is_file()
    baseline = load_baseline(target)
    assert baseline["job_id"] == "job-1"
    assert load_baseline(tmp_path / "missing.json") is None
