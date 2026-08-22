"""Autotest Service：常驻评测服务（judger）。

v1.1 §3 冻结：
- 从场景 body 字段加载本体资产，经 INIT 下发给算法；
- 场景装配时校验 produces ⊇ consumes（fail fast）；
- 插件导入改为命名空间路径（plugins.pipe.slam / plugins.nav2d）。
"""
from __future__ import annotations

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
from ..manifest import load_algorithm_manifest
from ..protocol import topics
from ..registry import get_checker, get_dataset, load_plugin, validate_produces_consumes
from ..scenario import load_scenario
from .artifacts import ArtifactRecorder, artifacts_root
from .job import Job, result_to_dict

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

    # ---- 公共业务面（tzcomm 服务与 HTTP 面共享同一 Jobs 池） ----
    def submit(self, request: dict) -> dict:
        """提交评测：立即返回 job_id，异步执行。"""
        try:
            manifest = load_algorithm_manifest(request["manifest"])
            scenario_path = request.get("scenario") or manifest.scenario
            if scenario_path and not Path(scenario_path).is_absolute():
                # 相对路径相对算法仓库根（manifest 所在目录）解析，与 launch 工作目录语义一致
                scenario_path = str(Path(manifest.dir) / scenario_path)
            scenario = load_scenario(scenario_path)
            # 评测方可覆盖场景的 checker（交叉测试矩阵用）
            if request.get("checker"):
                scenario.checker = request["checker"]
            if request.get("checker_config"):
                scenario.checker_config = request["checker_config"]
        except Exception as exc:  # noqa: BLE001 配置错误直接回传给 client
            return {"error": f"{type(exc).__name__}: {exc}"}

        job_id = f"autotest-{uuid.uuid4().hex[:8]}"
        raw_rate = request.get("clock_rate")
        clock_rate = 1.0 if raw_rate is None else raw_rate  # 默认 1.0=实时复现；显式 0/None 由 Loader 视为全速
        job = Job(
            job_id=job_id,
            manifest=manifest,
            scenario=scenario,
            session_id=f"autotest-{uuid.uuid4().hex[:8]}",
            clock_rate=clock_rate,
        )
        with self._jobs_lock:
            self._jobs[job_id] = job
        threading.Thread(target=self._run_job, args=(job,), daemon=True).start()
        return {"job_id": job_id}

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
        """轮询进度/结果。"""
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job is None:
            return {"error": f"未知 job_id: {job_id!r}"}
        with job.lock:
            return {
                "job_id": job.job_id,
                "status": "done" if job.done.is_set() else "running",
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

    # ---- tzcomm 面适配 ----
    def _on_control(self, request: dict) -> dict:
        command = request.get("command")
        if command:
            return self.command(request.get("job_id", ""), command, int(request.get("n", 1)))
        return self.submit(request)

    def _on_job_status(self, request: dict) -> dict:
        return self.job_status(request.get("job_id", ""))

    def _run_job(self, job: Job) -> None:
        proc = None
        recorder = ArtifactRecorder(artifacts_root(), job.job_id)
        try:
            scenario = job.scenario
            manifest = job.manifest

            recorder.log(f"提交: manifest={manifest.dir} body={scenario.body} dataset={scenario.dataset_type}")

            # 0) 加载本体资产（v1.1 §8：body 必填）
            try:
                body = load_body(_REPO_ROOT / "body" / f"{scenario.body}.yaml")
            except BodyError as exc:
                raise ValueError(f"本体资产加载失败: {exc}") from exc

            # 1) 拉起算法，注入会话环境变量（算法据 AUTOTEST_SESSION/TOPICS 建接口）
            proc = launch_algorithm(manifest, job.session_id)

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
            runner = session_cls(world, checker, body=body, session_id=job.session_id, control=job.control)
            try:

                def _on_testcase(testcase_id: str, result: Any) -> None:
                    with job.lock:
                        job.results.append(result_to_dict(result))
                    passed = result.score.passed if result.score else None
                    metrics = result.score.metrics if result.score else None
                    recorder.log(
                        f"testcase {testcase_id}: passed={passed} metrics={metrics} "
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
        except Exception as exc:  # noqa: BLE001 结果回传给 client
            with job.lock:
                job.error = f"{type(exc).__name__}: {exc}"
            recorder.log(f"评测失败: {job.error}")
        finally:
            if proc is not None:
                stop_process(proc)
            with job.lock:
                job.done.set()
            recorder.save_report(
                {
                    "job_id": job.job_id,
                    "session_id": job.session_id,
                    "manifest": {
                        "launch": job.manifest.launch,
                        "scenario": job.manifest.scenario,
                        "image": job.manifest.image,
                    },
                    "scenario": {
                        "body": job.scenario.body,
                        "dataset_type": job.scenario.dataset_type,
                    },
                    "clock_rate": job.clock_rate,
                    "results": list(job.results),
                    "error": job.error,
                    "comm_health": job.comm_health,
                    "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            recorder.close()

    def spin(self) -> None:
        self._node.spin()

    def close(self) -> None:
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
