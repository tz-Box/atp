"""回归报告：评测结果（report.json）与历史基线（baseline.json）对比。

基线 = 某次评测的 report.json，存为 artifacts/baseline.json；
当前评测按 testcase 对齐，逐指标对比（变好/变差/持平）并渲染 markdown 报告。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


def compare(baseline: dict, current: dict) -> list[dict]:
    """按 testcase 对齐对比两轮评测，返回每 testcase 的对比明细。"""
    base_by_tc = {r["testcase_id"]: r for r in baseline.get("results", [])}
    rows: list[dict] = []
    for r in current.get("results", []):
        row = {"testcase_id": r["testcase_id"], "current": r}
        old = base_by_tc.get(r["testcase_id"])
        if old is None:
            row["change"] = "new"
            rows.append(row)
            continue

        deltas: dict[str, float] = {}
        new_metrics = r.get("metrics") or {}
        old_metrics = old.get("metrics") or {}
        for key in set(new_metrics) | set(old_metrics):
            if key in new_metrics and key in old_metrics:
                deltas[key] = round(float(new_metrics[key]) - float(old_metrics[key]), 6)
        row["baseline"] = old
        row["deltas"] = deltas
        # 指标越小越好 → 差值为负 = 变好
        if r.get("passed") and not old.get("passed"):
            row["change"] = "improved"
        elif not r.get("passed") and old.get("passed"):
            row["change"] = "regressed"
        elif deltas and all(v <= 0 for v in deltas.values()):
            row["change"] = "improved"
        elif deltas and all(v >= 0 for v in deltas.values()):
            row["change"] = "worse"
        else:
            row["change"] = "same"
        rows.append(row)
    return rows


def _fmt(value: Optional[float]) -> str:
    return f"{value:.4f}" if isinstance(value, (int, float)) else "-"


def render_markdown(current: dict, rows: list[dict]) -> str:
    """渲染回归报告（markdown）：含评测 meta 与逐 testcase 对比。"""
    lines = [f"## 评测报告：{current.get('job_id', '-')}", ""]
    if current.get("error"):
        lines += ["**评测失败**", "", f"```\n{current['error']}\n```", ""]
        return "\n".join(lines)

    lines += ["| testcase | 结论 | 变化 | 当前指标 | 基线指标 | 差值 |", "|---|---|---|---|---|---|"]
    for row in rows:
        r = row["current"]
        passed = "✅" if r.get("passed") else "❌"
        cur = ", ".join(f"{k}={_fmt(v)}" for k, v in (r.get("metrics") or {}).items()) or "数据流验证"
        old = row.get("baseline")
        base_txt = (
            ", ".join(f"{k}={_fmt(v)}" for k, v in (old.get("metrics") or {}).items())
            if old else "-"
        )
        delta_txt = (
            ", ".join(f"{k}={v:+.4f}" for k, v in row.get("deltas", {}).items())
            if row.get("deltas") else "-"
        )
        lines.append(
            f"| {row['testcase_id']} | {passed} | {row['change']} | {cur} | {base_txt} | {delta_txt} |"
        )
    lines.append("")
    return "\n".join(lines)


def save_baseline(report_dir: Path, baseline_path: Optional[Path] = None) -> Path:
    """把某次评测的 report.json 存为基线，返回基线路径。"""
    report = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    target = baseline_path or report_dir.parent / "baseline.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_baseline(path: Optional[Path]) -> Optional[dict]:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
