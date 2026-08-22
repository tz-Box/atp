"""测试内容：commcheck 纯函数——阈值告警、health 汇总、daemon 失败归因、CLI 退出码。"""
from __future__ import annotations

import json

from autotest import commcheck


# ---- assess_stats 阈值判定 ----
def test_assess_stats_none_no_warning() -> None:
    assert commcheck.assess_stats(None, "Service") == []


def test_assess_stats_below_threshold() -> None:
    stats = {"msgs": 1000, "lost": 1, "loss_rate": 0.001, "msgs_1s": 10, "lost_1s": 0, "loss_rate_1s": 0.0}
    assert commcheck.assess_stats(stats, "Service") == []


def test_assess_stats_above_threshold() -> None:
    stats = {"msgs": 100, "lost": 5, "loss_rate": 0.048, "msgs_1s": 10, "lost_1s": 0, "loss_rate_1s": 0.0}
    warnings = commcheck.assess_stats(stats, "SUT")
    assert len(warnings) == 1
    assert "SUT" in warnings[0] and "4.80%" in warnings[0]


def test_assess_stats_1s_window() -> None:
    stats = {"msgs": 10000, "lost": 50, "loss_rate": 0.005, "msgs_1s": 10, "lost_1s": 1, "loss_rate_1s": 0.091}
    warnings = commcheck.assess_stats(stats, "Service")
    assert any("1s" in w for w in warnings)


# ---- build_health 汇总 ----
def test_build_health_merges_sides() -> None:
    service = {"side": "service", "msgs": 100, "lost": 0, "loss_rate": 0.0,
               "msgs_1s": 0, "lost_1s": 0, "loss_rate_1s": 0.0}
    sut = {"side": "sut", "msgs": 50, "lost": 3, "loss_rate": 0.057,
           "msgs_1s": 0, "lost_1s": 0, "loss_rate_1s": 0.0}
    health = commcheck.build_health(service, sut)
    assert health["service"] is service
    assert health["sut"] is sut
    assert len(health["warnings"]) == 1
    assert "SUT" in health["warnings"][0]


def test_build_health_sut_absent_no_false_alarm() -> None:
    """SUT 未回传自统计（老 SDK/原生实现）时不产生 SUT 侧告警。"""
    service = {"side": "service", "msgs": 100, "lost": 0, "loss_rate": 0.0,
               "msgs_1s": 0, "lost_1s": 0, "loss_rate_1s": 0.0}
    health = commcheck.build_health(service, None)
    assert health["sut"] is None
    assert health["warnings"] == []


# ---- snapshot_node（stub Node，不依赖真实 tzcomm） ----
def test_snapshot_node() -> None:
    class _StubNode:
        def network_stats(self) -> dict:
            return {"msgs": 42, "lost": 1, "loss_rate": 1 / 43,
                    "msgs_1s": 5, "lost_1s": 0, "loss_rate_1s": 0.0}

    snap = commcheck.snapshot_node(_StubNode(), "service")
    assert snap["side"] == "service"
    assert snap["msgs"] == 42 and snap["lost"] == 1
    assert snap["loss_rate"] == round(1 / 43, 4)
    assert "ts" in snap


# ---- daemon 失败归因（连不通的端口） ----
def test_check_daemon_unreachable_has_hint(monkeypatch) -> None:
    monkeypatch.setenv("TZCOMM_DAEMON_ADDR", "127.0.0.1:1")  # 1 号端口必然拒绝
    result = commcheck.check_daemon(timeout=0.5)
    assert not result.ok
    assert "daemon" in result.name
    assert result.hint and "daemon" in result.hint


def test_run_checks_short_circuits_when_daemon_down(monkeypatch) -> None:
    monkeypatch.setenv("TZCOMM_DAEMON_ADDR", "127.0.0.1:1")
    report = commcheck.run_checks()
    assert report["ok"] is False
    assert [c["name"] for c in report["checks"]] == ["daemon", "pubsub", "service"]
    assert report["checks"][0]["ok"] is False
    # daemon 不通时后两级短路跳过（不浪费各自超时）
    assert all("skipped" in c.get("error", "") for c in report["checks"][1:])


def test_cli_json_exit_code(monkeypatch, capsys) -> None:
    monkeypatch.setenv("TZCOMM_DAEMON_ADDR", "127.0.0.1:1")
    rc = commcheck.main(["--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
