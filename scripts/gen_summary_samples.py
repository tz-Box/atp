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

from autotest.server.callback import build_metrics, summarize  # noqa: E402


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
    # ★expect=fail 却通过了：不是失败，是**判据坏了**（A11 四态里的第四态）
    "unexpected_pass": _rep(
        [{"testcase_id": "degraded:tc0", "passed": True, "metrics": {"ate_rmse": 0.01}, "n_records": 20}],
        [{"id": "degraded", "expect": "fail"}]),
}

_NOTE = ("由 autotest.server.callback 的真实代码路径生成（scripts/gen_summary_samples.py），非手编。"
         "★消费方应读 metrics 取事实；本文件的 summary 仅供文本兜底路径做回归。"
         "summary 是给人看的散文，其措辞不构成接口承诺。")


def main() -> int:
    out = {k: {"summary": summarize(v), "metrics": build_metrics(v)} for k, v in CASES.items()}
    out["_说明"] = _NOTE
    target = ROOT / "tests" / "fixtures" / "summary_samples.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {target.relative_to(ROOT)}（{len(CASES)} 个样本）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
