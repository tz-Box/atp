"""测试内容：ArtifactRecorder 评测留痕（session.log + report.json）。"""
from __future__ import annotations

import json

from autotest.server.artifacts import ArtifactRecorder, artifacts_root


def test_recorder_writes_log_and_report(tmp_path) -> None:
    recorder = ArtifactRecorder(tmp_path, "job-1")
    recorder.log("提交: manifest=/tmp/x scenario=slam")
    recorder.log("testcase tc0: passed=True metrics={'ate_rmse': 0.0}")
    recorder.save_report({"job_id": "job-1", "results": [{"testcase_id": "tc0"}]})
    recorder.close()

    log = (tmp_path / "job-1" / "session.log").read_text(encoding="utf-8")
    assert log.count("\n") == 2
    assert "testcase tc0" in log

    report = json.loads((tmp_path / "job-1" / "report.json").read_text(encoding="utf-8"))
    assert report["job_id"] == "job-1"
    assert report["results"][0]["testcase_id"] == "tc0"


def test_recorder_dir_property(tmp_path) -> None:
    recorder = ArtifactRecorder(tmp_path, "job-2")
    assert recorder.dir == tmp_path / "job-2"
    recorder.close()


def test_artifacts_root_env(monkeypatch, tmp_path) -> None:
    target = tmp_path / "my-artifacts"
    monkeypatch.setenv("AUTOTEST_ARTIFACTS_DIR", str(target))
    assert artifacts_root() == target
