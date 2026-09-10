#!/usr/bin/env python3
"""生成 tests/fixtures/summary_samples.json —— 供消费方（Hub console 的文本兜底）做回归夹具。

**为什么由生产方生成、而不是消费方手编**：2026-09-05 的实例——Hub 的逐场景解析正则
从 08-26 就无法正确处理 `场景id:用例id` 形态，潜伏十天没被发现；根因是
**手编夹具只能覆盖「我以为的格式」，而那恰好是窄的那个**。

同一天生成这份夹具时，又当场暴露了 ATP 自己的一个 bug：`<runtime>` / `<scenario>`
这类失败条目没有 metrics，此前被一律标成「数据流验证」——而它们恰恰是装配/环境失败，
是最需要被看见的那类，且消费方正则只认 passed|failed，这些行连匹配都匹配不到。
**造夹具这个动作本身就是测试。**

用法：`python3 scripts/gen_summary_samples.py`（改了 summarize/build_metrics 后重跑）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from autotest.eval.checker import Judgement, Score  # noqa: E402
from autotest.server.callback import build_metrics, changes_of, summarize  # noqa: E402
from autotest.server.job import judgements_to_dicts  # noqa: E402
from autotest.server.report import compare  # noqa: E402


def _rep(results, scenarios=None, error=None):
    return {"job_id": "j1", "error": error, "comm_health": {"warnings": []},
            "results": results, "scenarios": scenarios or []}


CASES = {
    # 多场景 + expect（A11 主形态）
    "multi_with_expect": _rep(
        [{"testcase_id": "smoke:tc0", "passed": True, "metrics": {"ate_rmse": 0.0}, "n_records": 20},
         {"testcase_id": "degraded:tc0", "passed": False, "metrics": {"ate_rmse": 0.2087}, "n_records": 20},
         {"testcase_id": "degraded:tc1", "passed": False, "metrics": {"ate_rmse": 0.2104}, "n_records": 20}],
        [{"id": "smoke", "expect": "pass"}, {"id": "degraded", "expect": "fail"}]),
    # 多场景 不带 expect（存量仓；消费方的四态应自动退化成两态）
    "multi_no_expect": _rep(
        [{"testcase_id": "small_push:small_push", "passed": True, "metrics": {"survived": 1.0}, "n_records": 501},
         {"testcase_id": "full:medium_push", "passed": True, "metrics": {"survived": 1.0}, "n_records": 501}],
        [{"id": "small_push"}, {"id": "full"}]),
    # 单场景：testcase_id **无前缀**（前缀只在一次跑多个场景时才出现）
    "single_scenario": _rep(
        [{"testcase_id": "tc0", "passed": True, "metrics": {"ate_rmse": 0.0}, "n_records": 20}],
        [{"id": "smoke", "expect": "pass"}]),
    # 场景省略 checker：passed=None，只验数据流，**不是失败**
    "dataflow_only": _rep(
        [{"testcase_id": "probe:tc0", "passed": None, "metrics": None, "n_records": 42}],
        [{"id": "probe"}]),
    # ATP 自身失败（checkout/manifest 等）：summary 不是场景形态
    "atp_error": _rep([], [], error="CheckoutError: repo 不可达（本地路径不存在）: x/y"),
    # 环境准备失败 + 场景装配失败：无 metrics 但确是失败
    "runtime_and_scenario_failure": _rep(
        [{"testcase_id": "<runtime>", "passed": False, "metrics": None, "n_records": 0,
          "error": "运行环境准备失败: venv 创建失败"},
         {"testcase_id": "bad:<scenario>", "passed": False, "metrics": None, "n_records": 0,
          "error": "场景 bad 执行失败: RegistryError"}],
        [{"id": "bad"}]),
    # ★声明了却一条 testcase 都没产出：actual="none"，恒判不符合预期。
    # 不这么做它会从报文里静默消失——met/unmet 都不加一，结论照报 success。
    "scenario_declared_but_empty": _rep(
        [{"testcase_id": "smoke:tc0", "passed": True, "metrics": {"ate_rmse": 0.0}, "n_records": 20}],
        [{"id": "smoke", "expect": "pass"}, {"id": "empty", "expect": "pass"}]),
    # ★expect=fail 却通过了：不是失败，是**判据坏了**（A11 四态里的第四态）
    "unexpected_pass": _rep(
        [{"testcase_id": "degraded:tc0", "passed": True, "metrics": {"ate_rmse": 0.01}, "n_records": 20}],
        [{"id": "degraded", "expect": "fail"}]),
}

# ---- vs_baseline 方向修复样本（M 2026-09-11 按缺陷批准）----
# 两层各守边界：vs_baseline_detail 里数值+delta 永远给（事实层）；
# judgement 的 better/worse 只在被测仓 scenario.yaml 声明了 direction 时出现（判定层）。
# 分类枚举 new/improved/regressed/worse/mixed/same/undetermined/no_comparable——
# same 只表示「确实没变化」；缺方向声明=undetermined；无可比指标=no_comparable。

_INVP_DIRECTIONS = {"survived": "higher", "survival_time": "higher",
                    "upright_ratio": "higher", "max_abs_theta": "lower",
                    "settle_error": "lower"}


def _invp_result(tc, metrics, passed=True, directions=_INVP_DIRECTIONS):
    entry = {"testcase_id": tc, "passed": passed, "metrics": metrics, "n_records": 501}
    if directions:
        entry["directions"] = directions
    return entry


BASELINE_CASES = {
    # ★修复动机的原型：大好指标降 + 小好指标升 = 典型真劣化（各指标仍在阈值内，
    # passed 不翻转）。修复前符号有涨有跌走 else 落 `same`——完全不可见；
    # 而 upright_ratio 0.95→0.55 若碰上小好指标恰好持平，还会被报成 improved（反了）。
    # 现在方向已声明 → worse，三个 judgement 都能在 detail 里核对。
    "vsb_true_regression_now_visible": (
        _rep([_invp_result("full:tc0", {"survived": 1.0, "survival_time": 10.0,
                                        "upright_ratio": 0.95, "max_abs_theta": 0.05,
                                        "settle_error": 0.010}, directions=None)]),
        _rep([_invp_result("full:tc0", {"survived": 1.0, "survival_time": 10.0,
                                        "upright_ratio": 0.55, "max_abs_theta": 0.19,
                                        "settle_error": 0.018})],
             [{"id": "full", "expect": "pass"}]),
    ),
    # 新八态一次给全：improved（声明且同向变好）/ undetermined（指标变了但没声明方向）
    # / mixed（声明的指标有好有坏）/ same（确实没变化）/ no_comparable（无可比指标）/ new
    "vsb_all_states": (
        _rep([
            _invp_result("a:tc0", {"survived": 0.90, "settle_error": 0.020}, directions=None),
            {"testcase_id": "a:tc1", "passed": True, "metrics": {"foo": 1.0}, "n_records": 10},
            _invp_result("a:tc2", {"survived": 0.90, "settle_error": 0.020}, directions=None),
            _invp_result("a:tc3", {"survived": 0.90, "settle_error": 0.020}, directions=None),
            {"testcase_id": "a:tc4", "passed": None, "metrics": None, "n_records": 42},
        ]),
        _rep([
            _invp_result("a:tc0", {"survived": 0.98, "settle_error": 0.012}),   # improved
            {"testcase_id": "a:tc1", "passed": True, "metrics": {"foo": 1.3},   # undetermined
             "n_records": 10},
            _invp_result("a:tc2", {"survived": 0.95, "settle_error": 0.031}),   # mixed
            _invp_result("a:tc3", {"survived": 0.90, "settle_error": 0.020}),   # same
            {"testcase_id": "a:tc4", "passed": None, "metrics": None,           # no_comparable
             "n_records": 42},
            _invp_result("a:tc5", {"survived": 1.0, "settle_error": 0.009}),    # new
        ], [{"id": "a", "expect": "pass"}]),
    ),
}

# ---- A11 批 1 样本（testcases_detail）----
# 判据经真实装配路径（Score / judgements_to_dicts）产出，与 server._on_testcase 同一函数；
# 判据级 expected 来自被测仓 metrics:{名:{expect}} 声明，缺省 pass。

def _tc_row(tc, score: Score, expects=None, n=20):
    """模拟 server._on_testcase 的随行携带（同一 judgements_to_dicts）。"""
    row = {"testcase_id": tc, "metrics": score.metrics or None,
           "passed": score.passed if (score.judgements or score.metrics) else None,
           "n_records": n}
    if score.judgements:
        row["judgements"] = judgements_to_dicts(score, expects or {})
        row["criteria_declared"] = len(score.judgements)
    return row


def _slam_score(ate, rpe, ate_t=0.05, rpe_t=0.1):
    from autotest.eval.checker import judged
    return Score.from_judgements(
        metrics={"ate_rmse": ate, "rpe_rmse": rpe},
        judgements=[judged("ate_rmse", f"ate_rmse <= {ate_t}", ate <= ate_t, ate),
                    judged("rpe_rmse", f"rpe_rmse <= {rpe_t}", rpe <= rpe_t, rpe)])


TCD_CASES = {
    # ★判据级 expect:fail 的体检用例:degraded 场景声明 ate_rmse 必须超阈值。
    # 判据级 met=true(预期内失败)→ 用例 state=met;原始 passed=False 不改写;
    # 场景级 expect 也为 fail(两级声明由被测仓自洽),整次评测 success。
    "tcd_expect_fail_probe": _rep(
        [_tc_row("smoke:tc0", _slam_score(0.012, 0.008)),
         _tc_row("degraded:tc0", _slam_score(0.2087, 0.008),
                 expects={"ate_rmse": "fail"})],
        [{"id": "smoke", "expect": "pass"}, {"id": "degraded", "expect": "fail"}]),
    # ★actual:none:数据没到,checker 走 Score.not_run——判据以声明清单在场、
    # 全 none、无 value 键;用例 state=not_run(「没法评」≠「判据没过」,不标红)。
    # 另配一条数据流验证用例(零判据,也归 not_run,criteria_declared=0)。
    "tcd_not_run_and_dataflow": _rep(
        [_tc_row("full:tc0", _slam_score(0.012, 0.008)),
         _tc_row("full:tc1", Score.not_run((("ate_rmse", "ate_rmse <= 0.05"),
                                            ("rpe_rmse", "rpe_rmse <= 0.1")))),
         {"testcase_id": "probe:tc0", "passed": None, "metrics": None, "n_records": 42}],
        [{"id": "full", "expect": "pass"}, {"id": "probe", "expect": "pass"}]),
}

_NOTE = ("由 autotest.server.callback 的真实代码路径生成（scripts/gen_summary_samples.py），非手编。"
         "★消费方应读 metrics 取事实；本文件的 summary 仅供文本兜底路径做回归。"
         "summary 是给人看的散文，其措辞不构成接口承诺。")


def main() -> int:
    out = {k: {"summary": summarize(v), "metrics": build_metrics(v)} for k, v in CASES.items()}
    for k, (baseline, current) in BASELINE_CASES.items():
        rows = compare(baseline, current)
        changes = changes_of(rows)
        out[k] = {"summary": summarize(current, changes),
                  "metrics": build_metrics(current, changes, rows)}
    for k, rep in TCD_CASES.items():
        out[k] = {"summary": summarize(rep), "metrics": build_metrics(rep)}
    out["_说明"] = _NOTE
    target = ROOT / "tests" / "fixtures" / "summary_samples.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {target.relative_to(ROOT)}"
          f"（{len(CASES) + len(BASELINE_CASES) + len(TCD_CASES)} 个样本）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
