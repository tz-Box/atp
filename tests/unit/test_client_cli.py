"""client CLI 单测。

- 路径解析（M-D2 修复）：Service 为独立进程（systemd），cwd 与 client 不同——
  run 以 cwd 为基准，matrix 以矩阵文件所在目录为基准解析为绝对路径；
- CI 支撑（M-D3）：run --json 携带 job_id（定位 Service 侧产物），
  report --json 输出机读回归对比（changes 计数供回调摘要携带 vs_baseline）。
"""
from __future__ import annotations

import json

from autotest.client import cli


class _FakeNode:
    def __init__(self, *args, **kwargs):
        pass

    def close(self):
        pass


def _capture(monkeypatch, result: dict) -> dict:
    """截获发往 Service 的 request（不经真实 daemon）。"""
    captured: dict = {}

    def fake_submit(node, request):
        captured.update(request)
        return result

    monkeypatch.setattr(cli.tzcomm, "Node", _FakeNode)
    monkeypatch.setattr(cli, "_submit_and_wait", fake_submit)
    return captured


def test_run_resolves_manifest_to_abspath(monkeypatch, tmp_path):
    captured = _capture(monkeypatch, {"results": []})
    (tmp_path / "scenario.yaml").write_text("launch: x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert cli.run("scenario.yaml") == 0
    assert captured["manifest"] == str(tmp_path / "scenario.yaml")


def test_run_resolves_scenario_override_to_abspath(monkeypatch, tmp_path):
    captured = _capture(monkeypatch, {"results": []})
    monkeypatch.chdir(tmp_path)

    assert cli.run("m.yaml", scenario="scenes/a.yaml") == 0
    assert captured["manifest"] == str(tmp_path / "m.yaml")
    assert captured["scenario"] == str(tmp_path / "scenes" / "a.yaml")


def test_matrix_resolves_relative_to_matrix_dir(monkeypatch, tmp_path):
    captured = _capture(monkeypatch, {"results": []})
    repo = tmp_path / "algo_repo"
    repo.mkdir()
    matrix_file = tmp_path / "matrix.yaml"
    matrix_file.write_text(
        "algorithms:\n  - manifest: algo_repo/scenario.yaml\n", encoding="utf-8")
    monkeypatch.chdir(repo)  # 故意让 cwd ≠ 矩阵目录，验证基准是矩阵文件

    assert cli.matrix(str(matrix_file)) == 0
    assert captured["manifest"] == str(repo / "scenario.yaml")


def test_run_json_includes_job_id(monkeypatch, tmp_path, capsys):
    """run --json 携带 job_id：CI 后续 step 据此定位 artifacts/{job_id}/ 做回归对比。"""
    _capture(monkeypatch, {"job_id": "autotest-x1", "results": []})
    monkeypatch.chdir(tmp_path)

    assert cli.run("m.yaml", as_json=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["job_id"] == "autotest-x1"


def _write_artifacts(artifacts, job_id: str, results: list[dict]) -> None:
    job_dir = artifacts / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "report.json").write_text(
        json.dumps({"job_id": job_id, "results": results}), encoding="utf-8")


def test_report_json_compare_with_baseline(monkeypatch, tmp_path, capsys):
    """report --json：有基线时输出逐 testcase 对比与 changes 计数。"""
    artifacts = tmp_path / "artifacts"
    _write_artifacts(artifacts, "autotest-j1", [
        {"testcase_id": "a", "passed": True, "metrics": {"m": 1.0}},
        {"testcase_id": "b", "passed": False, "metrics": {"m": 2.0}},
    ])
    (artifacts / "baseline.json").write_text(json.dumps({"results": [
        {"testcase_id": "a", "passed": True, "metrics": {"m": 1.0}},
        {"testcase_id": "b", "passed": True, "metrics": {"m": 2.0}},
    ]}), encoding="utf-8")
    monkeypatch.setenv("AUTOTEST_ARTIFACTS_DIR", str(artifacts))

    assert cli.report("autotest-j1", as_json=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["job_id"] == "autotest-j1"
    assert out["has_baseline"] is True
    # a 指标持平（delta=0 → improved），b passed 反转 → regressed
    assert out["changes"] == {"improved": 1, "regressed": 1}
    assert len(out["rows"]) == 2


def test_report_json_no_baseline_marks_all_new(monkeypatch, tmp_path, capsys):
    """report --json：无基线时全部 testcase 标 new（首次评测落基线前）。"""
    artifacts = tmp_path / "artifacts"
    _write_artifacts(artifacts, "autotest-j2", [
        {"testcase_id": "a", "passed": True, "metrics": {"m": 1.0}},
        {"testcase_id": "b", "passed": True, "metrics": {"m": 2.0}},
    ])
    monkeypatch.setenv("AUTOTEST_ARTIFACTS_DIR", str(artifacts))

    assert cli.report("autotest-j2", as_json=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["has_baseline"] is False
    assert out["changes"] == {"new": 2}


def test_report_missing_artifacts_returns_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AUTOTEST_ARTIFACTS_DIR", str(tmp_path))
    assert cli.report("autotest-nope", as_json=True) == 2
