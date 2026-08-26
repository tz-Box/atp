"""Autotest Service：常驻评测服务（judger）。

v1.1 §3 冻结：
- 从场景 body 字段加载本体资产，经 INIT 下发给算法；
- 场景装配时校验 produces ⊇ consumes（fail fast）；
- 插件导入改为命名空间路径（plugins.pipe.slam / plugins.nav2d）。

v1.5 §4.8 并发语义（M-E5）：单 ATP 串行执行（评测机资源独占）——
所有提交（HTTP 面 /atp/evaluations 与 tzcomm 面 client run）进入同一 FIFO 队列，
由唯一 worker 逐个执行；多 ATP 池化归 Hub 调度，ATP 间互不感知。
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import tzcomm

from ..body import load_body, BodyError
from ..commcheck import build_health
from ..eval.closed_loop import ClosedLoopSession
from ..eval.runner import Runner
from ..launcher import launch_algorithm, stop_process
from ..manifest import (ScenarioUnknownError, deep_merge, is_scenario_path,
                        load_algorithm_manifest, select_scenarios)
from ..protocol import topics
from ..registry import get_checker, get_dataset, load_plugin, validate_produces_consumes
from ..scenario import Scenario, load_scenario
from .artifacts import ArtifactRecorder, artifacts_root
from .callback import finalize_evaluation
from .checkout import (
    CheckoutError, cleanup_stale, locate_manifest, prepare_checkout, remove_worktree,
)
from .evaluations import EvaluationStore
from .job import Job, result_to_dict
from .runtime import RuntimePrepareError, prepare_runtime

_JOB_TTL_SECONDS = 3600.0  # job 结果在池中保留时长（本版不清理，预留）

# 仓库根目录（src/autotest/server/server.py → parents[3]），body/ 资产按此定位
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _namespace(key: str) -> str:
    """注册键 → 插件命名空间（键去掉最后一段，如 pipe.slam.rosbag → pipe.slam）。"""
    return key.rsplit(".", 1)[0]


class AutotestService:
    """常驻评测服务：接受评测任务并异步执行（会话池）。"""

    def __init__(self, name: str = "autotest-service") -> None:
        self._node = tzcomm.Node(name)
        self._node.create_service(topics.control_service(), self._on_control)
        self._node.create_service(topics.job_status_service(), self._on_job_status)
        self._jobs: dict[str, Job] = {}
        self._jobs_lock = threading.Lock()
        # v1.5 §4.8（M-E1）：Hub 直连评测的 cid 幂等与状态持久化
        self._eval_store = EvaluationStore(artifacts_root() / "atp.db")
        # v1.5 §4.8（M-E5）：单 ATP 串行——唯一 worker 消费 FIFO 队列（None = 停止信号）
        self._queue: queue.Queue[Optional[Job]] = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True,
                                        name="autotest-worker")
        self._worker.start()
        # M-E2：清扫上次异常退出滞留的 worktree（workspaces/<job_id>）
        for path in cleanup_stale():
            print(f"[checkout] 清理滞留 worktree: {path}")

    # ---- 公共业务面（tzcomm 服务与 HTTP 面共享同一 Jobs 池） ----
    def submit(self, request: dict, eval_ctx: Optional[dict] = None,
               job_id: Optional[str] = None) -> dict:
        """提交评测：立即返回 job_id，异步执行。eval_ctx 见 Job（仅 Hub 直连通路携带）。

        job_id 缺省生成；Hub 直连通路提前生成传入（M-E2 worktree 目录以 job_id 命名，
        须在 checkout 前确定）。
        M-F2：request["scenario_ids"]（list|None）按 manifest scenarios 清单选择场景，
        逐 entry 加载场景文件并深合并覆盖（hyperparams/checker_config/dataset_config）；
        错误返回 {"error", "code"}（manifest_invalid / scenario_unknown，M-F3）。
        """
        try:
            manifest = load_algorithm_manifest(request["manifest"])
            entries: list[tuple[str, Scenario]] = []
            explicit_path = request.get("scenario")  # 场景文件路径（CLI --scenario 覆盖，旧语义）
            if explicit_path is None:
                # M-F2：清单路径——scenario_ids（None=全跑；存量仓清单=唯一 default 项 ≡ 旧行为）
                for entry in select_scenarios(manifest, request.get("scenario_ids")):
                    sc = load_scenario(str(Path(manifest.dir) / entry.scenario))
                    # 覆盖优先级：场景文件 < 清单项（深合并）< 评测方（交叉测试矩阵，最高）
                    sc.hyperparams = deep_merge(sc.hyperparams, entry.hyperparams)
                    sc.checker_config = deep_merge(sc.checker_config, entry.checker_config)
                    sc.dataset_config = deep_merge(sc.dataset_config, entry.dataset_config)
                    if request.get("checker"):
                        sc.checker = request["checker"]
                    if request.get("checker_config"):
                        sc.checker_config = request["checker_config"]
                    entries.append((entry.id, sc))
                if not entries:
                    return {"error": "manifest 无可用场景（scenarios 与 scenario 均空）",
                            "code": "manifest_invalid"}
                scenario = entries[0][1]
            else:
                scenario_path = explicit_path
                if scenario_path and not Path(scenario_path).is_absolute():
                    # 相对路径相对算法仓库根（manifest 所在目录）解析，与 launch 工作目录语义一致
                    scenario_path = str(Path(manifest.dir) / scenario_path)
                scenario = load_scenario(scenario_path)
                # 评测方可覆盖场景的 checker（交叉测试矩阵用）
                if request.get("checker"):
                    scenario.checker = request["checker"]
                if request.get("checker_config"):
                    scenario.checker_config = request["checker_config"]
                entries = [("default", scenario)]
        except ScenarioUnknownError as exc:
            return {"error": str(exc), "code": "scenario_unknown"}
        except FileNotFoundError as exc:
            return {"error": f"{type(exc).__name__}: {exc}", "code": "manifest_missing"}
        except Exception as exc:  # noqa: BLE001 配置错误直接回传给 client
            return {"error": f"{type(exc).__name__}: {exc}", "code": "manifest_invalid"}

        job_id = job_id or f"autotest-{uuid.uuid4().hex[:8]}"
        raw_rate = request.get("clock_rate")
        clock_rate = 1.0 if raw_rate is None else raw_rate  # 默认 1.0=实时复现；显式 0/None 由 Loader 视为全速
        job = Job(
            job_id=job_id,
            manifest=manifest,
            scenario=scenario,
            session_id=f"autotest-{uuid.uuid4().hex[:8]}",
            clock_rate=clock_rate,
            eval_ctx=eval_ctx,
            scenario_entries=entries,
        )
        with self._jobs_lock:
            self._jobs[job_id] = job
        self._queue.put(job)  # M-E5：入队即返，单 worker 串行消费
        return {"job_id": job_id}

    @property
    def evaluations(self) -> EvaluationStore:
        return self._eval_store

    def submit_evaluation(self, req: dict) -> dict:
        """v1.5 §4.8（M-E1/M-E2）：Hub 直连评测提交——checkout 备好现场后走公共 submit。

        req: correlation_id/repo/ref/sha?/check_type/scenario?/save_baseline/pms_task_id?
        返回 {"job_id", "sha"} / {"duplicate": True, "job_id"} / {"error", "code"?}（坐标类 4xx 语义）。
        M-E2：git URL/owner-repo → mirror 缓存 + worktree 隔离（目录以 job_id 命名，须先生成）；
        坐标/manifest 失败时 worktree 立即清理，不留现场。
        M-F2/M-F3：scenario 三态——null(全跑)|id|[ids] → 清单选择；路径值(含 / 或 .yaml 结尾) →
        旧语义(manifest 相对路径)；错误码 manifest_missing/manifest_invalid/scenario_unknown。
        """
        cid = req["correlation_id"]
        job_id = f"autotest-{uuid.uuid4().hex[:8]}"
        raw_scenario = req.get("scenario")
        # M-F2 形态分派：路径值 → manifest 相对路径（旧语义）；其余（id 字符串 / id 列表 / None）→ 清单选择
        scenario_path = raw_scenario if is_scenario_path(raw_scenario) else None
        scenario_ids = (None if raw_scenario is None or is_scenario_path(raw_scenario)
                        else ([raw_scenario] if isinstance(raw_scenario, str) else list(raw_scenario)))
        checkout = None
        try:
            checkout = prepare_checkout(req["repo"], req.get("ref"), job_id=job_id)
        except CheckoutError as exc:  # clone/fetch/ref 不可达等坐标错误（非 manifest 问题）
            return {"error": str(exc)}
        try:
            manifest_path = locate_manifest(checkout.repo_dir, scenario_path)
        except CheckoutError as exc:
            if checkout.worktree is not None:
                remove_worktree(checkout.worktree)
            return {"error": str(exc), "code": "manifest_missing"}
        submit_req: dict = {"manifest": str(manifest_path)}
        if scenario_ids is not None:
            submit_req["scenario_ids"] = scenario_ids
        reply = self.submit(submit_req, eval_ctx={
            "cid": cid, "repo": req["repo"], "ref": req.get("ref"), "sha": checkout.sha,
            "check_type": req.get("check_type") or "autotest",
            "scenario": raw_scenario if isinstance(raw_scenario, str) else (
                ",".join(raw_scenario) if raw_scenario else None),
            "save_baseline": bool(req.get("save_baseline")),
            "pms_task_id": req.get("pms_task_id"),
            "checkout": checkout,  # M-E2：worker 终态后清理 worktree
        }, job_id=job_id)
        if reply.get("error"):
            if checkout.worktree is not None:
                remove_worktree(checkout.worktree)
            return reply
        created = self._eval_store.create(
            cid=cid, job_id=reply["job_id"], repo=req["repo"], ref=req.get("ref"),
            sha=checkout.sha,
            check_type=req.get("check_type") or "autotest",
            scenario=(raw_scenario if isinstance(raw_scenario, str) or raw_scenario is None
                      else ",".join(raw_scenario)),
            save_baseline=bool(req.get("save_baseline")), pms_task_id=req.get("pms_task_id"),
        )
        if not created:  # 并发同 cid：PK 兜底，返回先到的 job（幂等语义不破）
            existing = self._eval_store.get_by_cid(cid)
            return {"duplicate": True, "job_id": existing["job_id"]}
        return {"job_id": reply["job_id"], "sha": checkout.sha}

    def command(self, job_id: str, command: str, n: int = 1) -> dict:
        """调试命令（暂停/单步/继续，M-B3）。"""
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job is None:
            return {"error": f"未知 job_id: {job_id!r}"}
        if job.done.is_set():
            return {"error": f"评测已结束: {job_id}"}
        if command == "pause":
            job.control.pause()
        elif command == "resume":
            job.control.resume()
        elif command == "step":
            if not job.control.step(n):
                return {"error": "非暂停状态，step 无效（先 pause）"}
        else:
            return {"error": f"未知调试命令: {command!r}"}
        return {"ok": True, "job_id": job_id, "run_state": job.control.state,
                "frames": job.control.frames_sent}

    def job_status(self, job_id: str) -> dict:
        """轮询进度/结果。status: queued(排队中)/running(执行中)/done(结束)。"""
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job is None:
            return {"error": f"未知 job_id: {job_id!r}"}
        with job.lock:
            if job.done.is_set():
                status = "done"
            elif job.started.is_set():
                status = "running"
            else:
                status = "queued"
            return {
                "job_id": job.job_id,
                "status": status,
                "run_state": job.control.state,
                "frames": job.control.frames_sent,
                "error": job.error,
                "results": list(job.results),
                "comm_health": job.comm_health,
            }

    @property
    def job_count(self) -> int:
        with self._jobs_lock:
            return len(self._jobs)

    @property
    def queue_depth(self) -> int:
        """排队深度（M-E4 /atp/health 探活字段；M-E5 串行队列）。"""
        return self._queue.qsize()

    # ---- tzcomm 面适配 ----
    def _on_control(self, request: dict) -> dict:
        command = request.get("command")
        if command:
            return self.command(request.get("job_id", ""), command, int(request.get("n", 1)))
        return self.submit(request)

    def _on_job_status(self, request: dict) -> dict:
        return self.job_status(request.get("job_id", ""))

    def _worker_loop(self) -> None:
        """M-E5 串行语义：唯一 worker 逐 job 消费 FIFO 队列（评测机资源独占）。"""
        while True:
            job = self._queue.get()
            if job is None:  # 停止信号（close）
                return
            job.started.set()
            self._run_job(job)

    def _run_job(self, job: Job) -> None:
        recorder = ArtifactRecorder(artifacts_root(), job.job_id)
        try:
            # M-F4：job 级运行环境准备（同一 checkout 现场全场景共享一次；host → {}）
            runtime_env = prepare_runtime(job.manifest, recorder.log)
            # M-F2：逐场景顺序执行（旧式单场景 = [("default", scenario)] 兜底，行为不变）；
            # 每场景独立 launch/session，场景异常记失败条目并继续后续场景（场景间相互独立）
            entries = job.scenario_entries or [("default", job.scenario)]
            multi = len(entries) > 1
            for entry_id, scenario in entries:
                prefix = f"{entry_id}:" if multi else ""
                session_id = f"{job.session_id}-{entry_id}" if multi else job.session_id
                self._run_scenario(job, entry_id, scenario, recorder, prefix, session_id,
                                   runtime_env)
        except RuntimePrepareError as exc:
            # 环境准备失败 = job 级失败（场景未执行；docker 未实现/venv 创建失败等）
            msg = f"运行环境准备失败: {exc}"
            with job.lock:
                job.error = msg
                job.results.append({
                    "testcase_id": "<runtime>", "metrics": None, "passed": False,
                    "n_records": 0, "error": msg,
                })
            recorder.log(msg)
        finally:
            with job.lock:
                job.done.set()
            report_payload = {
                "job_id": job.job_id,
                "session_id": job.session_id,
                "manifest": {
                    "launch": job.manifest.launch,
                    "scenario": job.manifest.scenario,
                    "image": job.manifest.image,
                    "runtime": job.manifest.runtime or {"type": "host"},  # M-F4 排障留痕
                },
                "scenario": {
                    "body": job.scenario.body,
                    "dataset_type": job.scenario.dataset_type,
                },
                # M-F2：全场景清单（id/body/dataset_type），多场景排障与 Hub 展示用
                "scenarios": [
                    {"id": eid, "body": sc.body, "dataset_type": sc.dataset_type}
                    for eid, sc in (job.scenario_entries or [("default", job.scenario)])
                ],
                "clock_rate": job.clock_rate,
                "results": list(job.results),
                "error": job.error,
                "comm_health": job.comm_health,
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            recorder.save_report(report_payload)
            # v1.5 §4.8：Hub 直连评测收尾——终态+摘要落档（M-E1）、基线滚动与主动回调（M-E3）
            if job.eval_ctx is not None:
                finalize_evaluation(job.eval_ctx, report_payload, self._eval_store,
                                    recorder.dir, recorder.log)
                # M-E2：评测落盘后清理 worktree 隔离现场（ATP_WORKTREE_KEEP=1 保留排查）
                checkout = job.eval_ctx.get("checkout")
                if checkout is not None and checkout.worktree is not None:
                    remove_worktree(checkout.worktree)
                    recorder.log(f"[checkout] worktree 已清理: {checkout.worktree.path}")
            recorder.close()

    def _run_scenario(self, job: Job, entry_id: str, scenario: Scenario,
                      recorder: ArtifactRecorder, prefix: str, session_id: str,
                      runtime_env: Optional[dict] = None) -> None:
        """单场景执行主体（M-F2 自 _run_job 抽取）：body 加载 → launch → world/checker → 推流打分。

        场景内异常 → 记该场景的失败条目（passed=False）+ job.error（首个），不抛出——
        调用方继续后续场景（场景间相互独立，CI 需要完整的多场景结果矩阵）。
        runtime_env：M-F4 运行环境注入（venv PATH 前置等；host 为 None/{}）。
        """
        proc = None
        try:
            manifest = job.manifest

            recorder.log(f"场景[{entry_id}]: manifest={manifest.dir} body={scenario.body} dataset={scenario.dataset_type}")

            # 0) 加载本体资产（v1.1 §8：body 必填）
            try:
                body = load_body(_REPO_ROOT / "body" / f"{scenario.body}.yaml")
            except BodyError as exc:
                raise ValueError(f"本体资产加载失败: {exc}") from exc

            # 1) 拉起算法，注入会话环境变量（算法据 AUTOTEST_SESSION/TOPICS 建接口）
            #    + M-F4 运行环境（venv PATH 前置等）
            proc = launch_algorithm(manifest, session_id, extra_env=runtime_env)

            # 2) 加载场景数据源与 checker（数据源工厂返回 IWorld：rosbag/rostopic/device 同接口）
            #    加载插件包触发注册（dataset 与 checker 可分属不同命名空间，都要加载）
            load_plugin(_namespace(scenario.dataset_type))
            if scenario.checker and _namespace(scenario.checker) != _namespace(scenario.dataset_type):
                load_plugin(_namespace(scenario.checker))
            world = get_dataset(scenario.dataset_type)(**scenario.dataset_config)
            checker = get_checker(scenario.checker)() if scenario.checker else None

            # 3) produces ⊇ consumes 校验（v1.1 §7.2 fail fast）
            if checker and checker.consumes:
                validate_produces_consumes(checker.consumes, world.produces)

            recorder.log(
                f"数据源: {scenario.dataset_type} testcases={world.testcases} "
                f"checker={scenario.checker or '无(数据流验证)'} body={body.name}"
            )

            # 4) 会话握手 + 推流/交互 + 打分（progress_cb 逐 testcase 上报部分结果 + 留痕）
            #    闭环 World（closed_loop=True，如 SimWorld）走 ClosedLoopSession，否则走开环 Runner
            session_cls = ClosedLoopSession if getattr(world, "closed_loop", False) else Runner
            runner = session_cls(world, checker, body=body, session_id=session_id, control=job.control)
            try:

                def _on_testcase(testcase_id: str, result: Any) -> None:
                    entry = result_to_dict(result)
                    entry["testcase_id"] = f"{prefix}{entry['testcase_id']}"  # M-F2 多场景前缀
                    with job.lock:
                        job.results.append(entry)
                    passed = result.score.passed if result.score else None
                    metrics = result.score.metrics if result.score else None
                    recorder.log(
                        f"testcase {prefix}{testcase_id}: passed={passed} metrics={metrics} "
                        f"records={len(result.records)}"
                    )

                runner.run(
                    world.testcases,
                    init_config={
                        "sensor_config": body.sensor_topics,
                        "hyperparams": {**manifest.hyperparams, **scenario.hyperparams},
                    },
                    checker_config=scenario.checker_config,
                    clock_rate=job.clock_rate,
                    progress_cb=_on_testcase,
                )
            finally:
                # 通信健康采集（close 前）：Service 侧快照 + SUT final 自统计 → comm_health
                service_stats = runner.comm_snapshot()
                sut_stats = ((runner.sut_final or {}).get("comm"))
                health = build_health(service_stats, sut_stats)
                with job.lock:
                    job.comm_health = health
                for warning in health["warnings"]:
                    recorder.log(f"[comm] WARNING: {warning}")
                runner.close()
        except Exception as exc:  # noqa: BLE001 场景级失败记条目并继续后续场景
            msg = f"{type(exc).__name__}: {exc}"
            with job.lock:
                if job.error is None:
                    job.error = msg
                job.results.append({
                    "testcase_id": f"{prefix}<scenario>",
                    "metrics": None, "passed": False, "n_records": 0,
                    "error": f"场景 {entry_id} 执行失败: {msg}",
                })
            recorder.log(f"场景[{entry_id}]失败: {msg}")
        finally:
            if proc is not None:
                stop_process(proc)

    def spin(self) -> None:
        self._node.spin()

    def close(self) -> None:
        self._queue.put(None)  # worker 停止信号（队列中未执行的 job 随进程退出丢弃）
        self._node.close()


def main() -> None:
    # 接管 tzcomm 诊断日志（重连/注册失败/回调异常等）：控制台 + ~/.tzcomm/app.log
    # 级别走 TZCOMM_LOG_LEVEL（默认 INFO），DEBUG 可看 TCP 对账细节
    tzcomm.setup_logging()
    service = AutotestService()
    try:
        service.spin()
    finally:
        service.close()
