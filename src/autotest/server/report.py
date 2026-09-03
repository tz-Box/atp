"""回归报告：评测结果（report.json）与历史基线（baseline.json）对比。

基线 = 某次评测的 report.json。**按 repo 隔离**（D1）：Hub 直连通路存
`artifacts/baselines/<repo-slug>.json`，本机 client 通路（无仓坐标）沿用
`artifacts/baseline.json`。全局单文件会让多算法仓互相覆盖基线，
双方回归对比同时退化为永久 `new`——且症状与"多场景前缀迁移首轮全记 new"
这一已知良性现象完全相同，因而不会被察觉。
当前评测按 testcase 对齐，逐指标对比（变好/变差/持平）并渲染 markdown 报告。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional


_GITHUB_COORD = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def normalize_repo(repo: str) -> str:
    """基线归属用的 repo 归一化。

    仅对 **GitHub 坐标形态**（`owner/repo`）转小写——GitHub 的 owner/repo 大小写不敏感，
    `tz-Box/cicd_test` 与 `tz-box/cicd_test` 是同一个仓，不归一会各自建一份基线，
    回归对比静默分裂（实测库中两种写法都出现过）。
    本地路径 / git URL 保持原样：Linux 路径大小写敏感，归一会把不同目录合并。
    """
    return repo.lower() if _GITHUB_COORD.match(repo) else repo


def baseline_slug(repo: str) -> str:
    """repo 坐标 → 基线文件名（可读 + 唯一）。

    repo 形态多样（`owner/repo` / 本地绝对路径 / git URL），故先归一大小写（见
    normalize_repo）、再做字符白名单转换，最后缀 8 位摘要——保证不同 repo 在白名单
    转换后同名时仍互不覆盖（如 `a/b` 与 `a_b`）。
    """
    canonical = normalize_repo(repo)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", canonical).strip("_.")[:60] or "repo"
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
    return f"{safe}-{digest}"


def baseline_path_for(repo: Optional[str], root: Path) -> Path:
    """基线文件路径（D1：按 repo 隔离）。

    repo 为空 = 本机 client 通路（`client run/report` 只认 manifest 路径，无仓坐标），
    沿用历史的 `artifacts/baseline.json`，行为不变。
    """
    if not repo:
        return root / "baseline.json"
    return root / "baselines" / f"{baseline_slug(repo)}.json"


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
        # 指标越小越好 → 差值为负 = 变好。passed 翻转优先于指标增减。
        if r.get("passed") and not old.get("passed"):
            row["change"] = "improved"
        elif not r.get("passed") and old.get("passed"):
            row["change"] = "regressed"
        elif deltas and all(v == 0 for v in deltas.values()):
            # 逐指标完全一致 → same。此分支须在 improved/worse 之前：
            # 全零同时满足 all(<=0) 与 all(>=0)，落到 improved 会把"什么都没变"
            # 报成"变好了"（D1 的回归测试撞出来的相邻缺陷）。
            row["change"] = "same"
        elif deltas and all(v <= 0 for v in deltas.values()):
            row["change"] = "improved"
        elif deltas and all(v >= 0 for v in deltas.values()):
            row["change"] = "worse"
        else:
            row["change"] = "same"  # 有涨有跌，或无可比指标
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
    target.parent.mkdir(parents=True, exist_ok=True)  # D1：按 repo 隔离时目录可能尚不存在
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_baseline(path: Optional[Path]) -> Optional[dict]:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
