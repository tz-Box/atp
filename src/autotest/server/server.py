"""Autotest Service：常驻评测服务（judger）。

进程模型：
- 常驻服务（`python -m autotest.server`），持有场景 + GT + checker；
- Client（`python -m autotest.client`）经 `autotest/control` 提交评测（算法 manifest），
  立即拿到 job_id（异步），再经 `autotest/job/status` 轮询进度/结果；
- Service 读算法 scenario.yaml（静态能力）→ 分配会话 → Launcher 拉起算法
  （注入 AUTOTEST_SESSION/AUTOTEST_TOPICS）→ 会话握手（INIT/READY）→ 推流 → 打分。

会话池：每个 job 一个独立会话（session_id 隔离），并发互不阻塞。
yaml 取代"注册握手"：算法能力由 manifest 声明，会话身份由 Service 注入。
"""
from __future__ import annotations

import importlib
import threading
import time
import uuid
from typing import Any, Optional

import tzcomm

from ..eval.runner import Runner
from ..launcher import launch_algorithm, stop_process
from ..manifest import load_algorithm_manifest
from ..protocol import topics
from ..registry import get_checker, get_dataset
from ..scenario import load_scenario
from .artifacts import ArtifactRecorder, artifacts_root
from .job import Job, result_to_dict

_JOB_TTL_SECONDS = 3600.0  # job 结果在池中保留时长（本版不清理，预留）


class AutotestService:
    """常驻评测服务：接受评测任务并异步执行（会话池）。"""

    def __init__(self, name: str = "autotest-service") -> None:
        self._node = tzcomm.Node(name)
        self._node.create_service(topics.control_service(), self._on_control)
        self._node.create_service(topics.job_status_service(), self._on_job_status)
        self._jobs: dict[str, Job] = {}
        self._jobs_lock = threading.Lock()

    # ---- Client → Service：提交评测（立即返回 job_id，异步执行） ----
    def _on_control(self, request: dict) -> dict:
        try:
            manifest = load_algorithm_manifest(request["manifest"])
            scenario_path = request.get("scenario") or manifest.scenario
            scenario = load_scenario(scenario_path)
            # 评测方可覆盖场景的 checker（交叉测试矩阵用）
            if request.get("checker"):
                scenario.checker = request["checker"]
            if request.get("checker_config"):
                scenario.checker_config = request["checker_config"]
        except Exception as exc:  # noqa: BLE001 配置错误直接回传给 client
            return {"error": f"{type(exc).__name__}: {exc}"}
        if scenario.module != manifest.module:
            return {
                "error": f"算法模块 {manifest.module!r} 与场景模块 {scenario.module!r} 不一致"
            }

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

    # ---- Client → Service：轮询进度/结果 ----
    def _on_job_status(self, request: dict) -> dict:
        job_id = request.get("job_id", "")
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job is None:
            return {"error": f"未知 job_id: {job_id!r}"}
        with job.lock:
            return {
                "job_id": job.job_id,
                "status": "done" if job.done.is_set() else "running",
                "error": job.error,
                "results": list(job.results),
            }

    def _run_job(self, job: Job) -> None:
        proc = None
        recorder = ArtifactRecorder(artifacts_root(), job.job_id)
        try:
            scenario = job.scenario
            manifest = job.manifest

            recorder.log(f"提交: manifest={manifest.dir} scenario={scenario.module}")

            # 1) 拉起算法，注入会话环境变量（算法据 AUTOTEST_SESSION/TOPICS 建接口）
            proc = launch_algorithm(manifest, job.session_id)

            # 2) 加载场景数据源与 checker（数据源工厂返回 IWorld：rosbag/rostopic/device 同接口）
            importlib.import_module(f"modules.{scenario.module}")  # 触发 dataset/checker 注册
            world = get_dataset(scenario.dataset_type)(**scenario.dataset_config)
            checker = get_checker(scenario.checker)() if scenario.checker else None

            recorder.log(
                f"数据源: {scenario.dataset_type} testcases={world.testcases} "
                f"checker={scenario.checker or '无(数据流验证)'}"
            )

            # 3) 会话握手 + 推流 + 打分（progress_cb 逐 testcase 上报部分结果 + 留痕）
            runner = Runner(world, checker, session_id=job.session_id)
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
                        "sensor_config": scenario.sensor_config,
                        "hyperparams": {**manifest.hyperparams, **scenario.hyperparams},
                    },
                    checker_config=scenario.checker_config,
                    clock_rate=job.clock_rate,
                    progress_cb=_on_testcase,
                )
            finally:
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
                        "module": job.manifest.module,
                        "launch": job.manifest.launch,
                        "scenario": job.manifest.scenario,
                        "image": job.manifest.image,
                    },
                    "scenario": {
                        "module": job.scenario.module,
                        "dataset_type": job.scenario.dataset_type,
                    },
                    "clock_rate": job.clock_rate,
                    "results": list(job.results),
                    "error": job.error,
                    "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            recorder.close()

    def spin(self) -> None:
        self._node.spin()

    def close(self) -> None:
        self._node.close()


def main() -> None:
    service = AutotestService()
    try:
        service.spin()
    finally:
        service.close()
