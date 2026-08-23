"""client CLI 路径解析单测（M-D2 修复）。

Service 为独立进程（systemd），cwd 与 client 不同：相对路径必须在 client 侧
解析为绝对路径——run 以 cwd 为基准，matrix 以矩阵文件所在目录为基准。
"""
from __future__ import annotations

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
