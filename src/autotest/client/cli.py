"""client 层：CLI 控制端（算法侧触发评测的入口）。

用法：
  run <算法scenario.yaml> [--scenario <场景>]     提交一次评测并等待结果
  matrix <test_matrix.yaml> [--json]              交叉测试矩阵（多算法 × 场景/checker）
  report <job_id> [--save-baseline]               输出回归报告（与历史基线对比）
  pause <job_id>                                  暂停喂帧（调试：世界冻结，SUT 安全等待）
  step <job_id> [-n N]                            暂停中单步放行 N 帧（默认 1）
  resume <job_id>                                 恢复全速

流程：
  1) 经 autotest/control 提交评测（manifest 路径），拿到 job_id（异步）；
  2) 经 autotest/job/status 轮询到完成；
  3) 打印结果（--json 输出结构化结果，供 CI 收集）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml
import tzcomm

from ..protocol import topics
from ..server.report import compare, load_baseline, render_markdown, save_baseline

_CONTROL_TIMEOUT = 3600.0
_POLL_INTERVAL = 1.0


def _emit_error(message: str, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": message}))
    else:
        print(f"评测失败: {message}", file=sys.stderr)
    return 1


def _submit_and_wait(node: tzcomm.Node, request: dict) -> dict:
    """提交评测并轮询到完成，返回最终状态（job_id/results/error）。"""
    ctl = node.create_service_client(topics.control_service())
    resp = ctl.call(request, timeout=_CONTROL_TIMEOUT)
    if resp.get("error"):
        return {"error": resp["error"]}
    # job_id 是调试入口（pause/step/resume）：打 stderr，保持 --json 的 stdout 干净
    print(f"job_id: {resp['job_id']}（调试：python3 -m autotest.client pause/step/resume <job_id>）", file=sys.stderr)

    status = node.create_service_client(topics.job_status_service())
    deadline = time.monotonic() + _CONTROL_TIMEOUT
    while time.monotonic() < deadline:
        state = status.call({"job_id": resp["job_id"]}, timeout=_CONTROL_TIMEOUT)
        if state.get("error"):
            return state
        if state["status"] == "done":
            return state
        time.sleep(_POLL_INTERVAL)
    return {"error": "评测超时"}


def _print_results(results: list[dict]) -> None:
    for r in results:
        if r.get("metrics"):
            print(f"{r['testcase_id']}: {r['metrics']} passed={r['passed']}")
        else:
            print(f"{r['testcase_id']}: 数据流验证（records={r['n_records']}，无 GT 评分）")


def run(manifest: str, scenario: str | None = None, clock_rate: float | None = None,
        as_json: bool = False) -> int:
    node = tzcomm.Node("autotest-client")
    try:
        # 路径以 client 侧 cwd 为基准解析为绝对路径：Service 是独立进程（systemd），
        # 其 cwd 与 client 不同（runner 上 workflow 从算法仓 checkout 目录执行）
        request = {"manifest": str(Path(manifest).resolve())}
        if clock_rate is not None:  # 未指定则省略，由 Service 用默认 1.0（实时复现）
            request["clock_rate"] = clock_rate
        if scenario:
            request["scenario"] = str(Path(scenario).resolve())
        state = _submit_and_wait(node, request)
    finally:
        node.close()

    if state.get("error"):
        return _emit_error(state["error"], as_json)
    if as_json:
        print(json.dumps({
            "ok": True,
            # job_id 供后续 step 定位 Service 侧产物（report 回归对比 / 调试命令）
            "job_id": state.get("job_id"),
            "results": state.get("results", []),
            "comm_health": state.get("comm_health"),
        }, ensure_ascii=False))
    else:
        _print_results(state.get("results", []))
        comm = state.get("comm_health")
        if comm and comm.get("warnings"):
            print(f"通信告警: {comm['warnings']}")
    return 0


def matrix(matrix_path: str, as_json: bool = False) -> int:
    matrix_file = Path(matrix_path).resolve()
    data = yaml.safe_load(matrix_file.read_text(encoding="utf-8"))
    entries = data.get("algorithms") or []
    if not entries:
        print("test_matrix.yaml 缺少 algorithms 列表", file=sys.stderr)
        return 2

    def _abs(p: str) -> str:
        # 相对路径以矩阵文件所在目录为基准（Service 为独立进程，cwd 不可依赖）
        q = Path(p)
        return str(q if q.is_absolute() else (matrix_file.parent / q).resolve())

    default_clock_rate = data.get("clock_rate")
    aggregate: list[dict] = []
    node = tzcomm.Node("autotest-matrix")
    try:
        for entry in entries:
            request = {"manifest": _abs(entry["manifest"])}
            rate = entry.get("clock_rate", default_clock_rate)
            if rate is not None:  # 未指定则省略，由 Service 用默认 1.0（实时复现）
                request["clock_rate"] = rate
            for key in ("scenario", "checker", "checker_config"):
                if entry.get(key):
                    request[key] = _abs(entry[key]) if key == "scenario" else entry[key]
            state = _submit_and_wait(node, request)
            aggregate.append({"manifest": entry["manifest"], **state})
    finally:
        node.close()

    if as_json:
        print(json.dumps({"ok": True, "algorithms": aggregate}, ensure_ascii=False))
        return 0
    print("=== 交叉测试矩阵结果 ===")
    for item in aggregate:
        tag = item["manifest"]
        if item.get("error"):
            print(f"[{tag}] 评测失败: {item['error']}")
        else:
            print(f"[{tag}]")
            for r in item.get("results", []):
                if r.get("metrics"):
                    print(f"  {r['testcase_id']}: {r['metrics']} passed={r['passed']}")
                else:
                    print(f"  {r['testcase_id']}: 数据流验证（records={r['n_records']}）")
    return 0


def debug_command(command: str, job_id: str, n: int = 1, as_json: bool = False) -> int:
    """发送调试命令（pause/step/resume）到 control 服务，返回退出码。"""
    node = tzcomm.Node("autotest-debug")
    try:
        ctl = node.create_service_client(topics.control_service())
        request: dict = {"command": command, "job_id": job_id}
        if command == "step":
            request["n"] = n
        resp = ctl.call(request, timeout=_CONTROL_TIMEOUT)
    finally:
        node.close()
    if resp.get("error"):
        return _emit_error(resp["error"], as_json)
    if as_json:
        print(json.dumps(resp, ensure_ascii=False))
    else:
        print(f"{command} {job_id}: run_state={resp['run_state']} frames={resp['frames']}")
    return 0


def report(job_id: str, baseline: str | None = None, save: bool = False,
           as_json: bool = False) -> int:
    artifacts_dir = Path(os.environ.get("AUTOTEST_ARTIFACTS_DIR", "artifacts"))
    report_dir = artifacts_dir / job_id
    report_path = report_dir / "report.json"
    if not report_path.is_file():
        print(f"未找到评测产物: {report_path}", file=sys.stderr)
        return 2
    current = json.loads(report_path.read_text(encoding="utf-8"))

    if save:
        target = save_baseline(report_dir)
        print(f"已保存基线: {target}")
        return 0

    baseline_path = Path(baseline) if baseline else artifacts_dir / "baseline.json"
    old = load_baseline(baseline_path)
    rows = compare(old, current) if old else [
        {"testcase_id": r["testcase_id"], "current": r, "change": "new"}
        for r in current.get("results", [])
    ]
    if as_json:
        # 机读对比结果（CI 用）：changes 计数供回调摘要携带 vs_baseline
        changes: dict[str, int] = {}
        for row in rows:
            changes[row["change"]] = changes.get(row["change"], 0) + 1
        print(json.dumps({
            "job_id": current.get("job_id", job_id),
            "has_baseline": old is not None,
            "changes": changes,
            "rows": rows,
        }, ensure_ascii=False))
    else:
        print(render_markdown(current, rows))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autotest", description="统一自动化测试服务 Client")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_parser = sub.add_parser("run", help="提交一次评测")
    run_parser.add_argument("manifest", help="算法 scenario.yaml 路径（算法根目录）")
    run_parser.add_argument("--scenario", default=None, help="覆盖场景 YAML 路径（默认用 manifest 声明）")
    run_parser.add_argument("--clock-rate", type=float, default=None, help="推流倍率（默认 1.0=实时复现原始帧率；2.0 加速；0 全速）")
    run_parser.add_argument("--json", action="store_true", help="以 JSON 输出结果（CI 用）")

    matrix_parser = sub.add_parser("matrix", help="提交交叉测试矩阵")
    matrix_parser.add_argument("matrix", help="test_matrix.yaml 路径")
    matrix_parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")

    report_parser = sub.add_parser("report", help="输出回归报告（与历史基线对比）")
    report_parser.add_argument("job_id", help="评测 job_id（对应 artifacts/{job_id}/report.json）")
    report_parser.add_argument("--baseline", default=None, help="基线 report.json 路径（默认 artifacts/baseline.json）")
    report_parser.add_argument("--save-baseline", action="store_true", help="把该次评测保存为基线")
    report_parser.add_argument("--json", action="store_true", help="以 JSON 输出机读对比结果（CI 用）")

    pause_parser = sub.add_parser("pause", help="暂停喂帧（调试：世界冻结，SUT 安全等待）")
    pause_parser.add_argument("job_id", help="评测 job_id")
    pause_parser.add_argument("--json", action="store_true", help="以 JSON 输出")

    step_parser = sub.add_parser("step", help="暂停中单步放行 N 帧")
    step_parser.add_argument("job_id", help="评测 job_id")
    step_parser.add_argument("-n", type=int, default=1, help="放行帧数（默认 1）")
    step_parser.add_argument("--json", action="store_true", help="以 JSON 输出")

    resume_parser = sub.add_parser("resume", help="恢复全速")
    resume_parser.add_argument("job_id", help="评测 job_id")
    resume_parser.add_argument("--json", action="store_true", help="以 JSON 输出")

    args = parser.parse_args(argv)

    if args.cmd == "run":
        return run(args.manifest, scenario=args.scenario, clock_rate=args.clock_rate, as_json=args.json)
    if args.cmd == "matrix":
        return matrix(args.matrix, as_json=args.json)
    if args.cmd == "report":
        return report(args.job_id, baseline=args.baseline, save=args.save_baseline,
                      as_json=args.json)
    if args.cmd == "pause":
        return debug_command("pause", args.job_id, as_json=args.json)
    if args.cmd == "step":
        return debug_command("step", args.job_id, n=args.n, as_json=args.json)
    if args.cmd == "resume":
        return debug_command("resume", args.job_id, as_json=args.json)
    parser.error(f"未知命令: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
