"""SUT SDK：算法侧基类。算法只覆写 on_init/on_reset/on_step/on_terminate。

进程模型：算法由评测服务启动，评测服务注入环境变量
AUTOTEST_SESSION（会话号）与 AUTOTEST_TOPICS（JSON 话题表），
算法据此建订阅/发布/服务，无需注册握手。
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

import tzcomm

from ..protocol import messages as msg
from ..protocol import topics
from ..protocol.schema import Action, Observation, Result


class SutBase:
    module: str = ""
    version: str = "0.1.0"
    required_sensors: dict = {}  # {类型: [实例名...]}，算法声明所需传感器

    def __init__(self, name: str, session_id: Optional[str] = None) -> None:
        self._node = tzcomm.Node(name)
        if session_id is None:
            # 生产路径：环境变量由评测服务注入
            session_id = os.environ.get("AUTOTEST_SESSION", "")
            session_topics = json.loads(os.environ.get("AUTOTEST_TOPICS", "{}"))
        else:
            # 进程内/测试路径：按 session_id 推导话题
            session_topics = {
                "obs": topics.obs_topic(session_id),
                "result": topics.result_topic(session_id),
                "action": topics.action_topic(session_id),
                "ctl": topics.ctl_service(session_id),
            }
        if not session_id or not session_topics:
            raise RuntimeError("缺少 AUTOTEST_SESSION/AUTOTEST_TOPICS（应由评测服务注入）")
        self.session_id = session_id
        self._result_pub = self._node.create_publisher(session_topics["result"], single_pub=True)
        self._action_pub = self._node.create_publisher(session_topics["action"], single_pub=True)
        self._node.create_subscription(session_topics["obs"], self._on_observation, qos=1)
        self._node.create_service(session_topics["ctl"], self._on_control)

    # ---- 算法需覆写的钩子 ----
    def on_init(self, config: dict[str, Any]) -> dict[str, Any]:
        return {
            "module": self.module,
            "version": self.version,
            "required_sensors": self.required_sensors,
        }

    def on_reset(self, testcase_meta: dict[str, Any]) -> None:
        pass

    def on_step(self, observation: Observation) -> Optional[Result | Action]:
        """开环回 Result（发 RESULT）；闭环回 Action（发 ACTION）。"""
        return None

    def on_terminate(self, reason: str) -> dict[str, Any]:
        return {}

    # ---- 协议处理（算法无需关心） ----
    def _on_control(self, request: dict) -> dict:
        message = msg.Message.from_dict(request)
        if message.type == msg.INIT:
            return msg.ready(self.session_id, self.on_init(message.payload)).to_dict()
        if message.type == msg.RESET:
            self.on_reset(message.payload)
            return msg.reset_ack(self.session_id, True).to_dict()
        if message.type == msg.TERMINATE:
            reason = message.payload.get("reason", "")
            return msg.final(self.session_id, self.on_terminate(reason)).to_dict()
        raise ValueError(f"未知控制消息: {message.type!r}")

    def _on_observation(self, request: dict) -> None:
        message = msg.Message.from_dict(request)
        observation, done = msg.parse_step(message)
        if done or observation is None:
            return
        reply = self.on_step(observation)
        if isinstance(reply, Result):
            self._result_pub.publish(msg.result(self.session_id, reply).to_dict())
        elif isinstance(reply, Action):
            self._action_pub.publish(msg.action(self.session_id, reply).to_dict())

    def spin(self) -> None:
        self._node.spin()

    def close(self) -> None:
        self._node.close()
