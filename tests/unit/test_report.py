"""测试内容：回归报告 compare / render / 基线存取。"""
from __future__ import annotations

from autotest.server.report import compare, load_baseline, render_markdown, save_baseline


def _report(results: list[dict]) -> dict:
    return {"job_id": "job-1", "results": results}


def test_compare_improved() -> None:
    baseline = _report([{"testcase_id": "tc0", "passed": True, "metrics": {"ate_rmse": 0.10}}])
    current = _report([{"testcase_id": "tc0", "passed": True, "metrics": {"ate_rmse": 0.05}}])
    rows = compare(baseline, current)
    assert rows[0]["change"] == "improved"
    assert rows[0]["deltas"]["ate_rmse"] == -0.05


def test_compare_regressed() -> None:
    baseline = _report([{"testcase_id": "tc0", "passed": True, "metrics": {"ate_rmse": 0.05}}])
    current = _report([{"testcase_id": "tc0", "passed": True, "metrics": {"ate_rmse": 0.12}}])
    rows = compare(baseline, current)
    assert rows[0]["change"] == "worse"
    assert rows[0]["deltas"]["ate_rmse"] == 0.07


def test_compare_passed_fallback() -> None:
    baseline = _report([{"testcase_id": "tc0", "passed": False, "metrics": {"ate_rmse": 0.05}}])
    current = _report([{"testcase_id": "tc0", "passed": True, "metrics": {"ate_rmse": 0.05}}])
    assert compare(baseline, current)[0]["change"] == "improved"


def test_compare_new_testcase() -> None:
    baseline = _report([{"testcase_id": "tc0", "passed": True}])
    current = _report([{"testcase_id": "tc1", "passed": True}])
    rows = compare(baseline, current)
    assert rows[0]["change"] == "new"
    assert "baseline" not in rows[0]


def test_render_markdown() -> None:
    rows = compare(
        _report([{"testcase_id": "tc0", "passed": True, "metrics": {"ate_rmse": 0.10}}]),
        _report([{"testcase_id": "tc0", "passed": True, "metrics": {"ate_rmse": 0.05}}]),
    )
    md = render_markdown(_report([{"testcase_id": "tc0", "passed": True,
                                   "metrics": {"ate_rmse": 0.05}}]), rows)
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
