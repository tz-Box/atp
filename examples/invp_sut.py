"""示例 SUT：倒立摆 PD 控制器（ctrl.invp 闭环通路）。

进程由 Service 经 Launcher 启动，会话身份来自环境变量
AUTOTEST_SESSION/AUTOTEST_TOPICS（由 Service 注入）。
控制增益经 INIT hyperparams 下发（manifest/scenario 可覆盖，键见 _DEFAULT_GAINS）。
"""
from __future__ import annotations

from autotest.protocol import messages as msg
from autotest.protocol.schema import decode_observation, encode_action
from autotest.registry import load_plugin
from autotest.sdk import SutBase

invp = load_plugin("ctrl.invp")  # 注册 ctrl.invp schema 编解码器

_DEFAULT_GAINS = {"kp": 30.0, "kd": 5.0, "kx": 1.0, "kxd": 2.0}


class PdPendulum(SutBase):
    """PD 状态反馈：F = kp·θ + kd·θ̇ + kx·x + kxd·ẋ（无内部状态，reset 无需清理）。"""

    module = "ctrl.invp"

    def on_init(self, config):
        p = config.get("hyperparams", {})
        self._gains = {k: float(p.get(k, v)) for k, v in _DEFAULT_GAINS.items()}
        return super().on_init(config)

    def on_step(self, observation):
        obs = decode_observation(observation["data"])  # InvpObs
        g = self._gains
        force = (
            g["kp"] * obs.theta + g["kd"] * obs.theta_dot
            + g["kx"] * obs.x + g["kxd"] * obs.x_dot
        )
        return msg.Action(
            "ctrl.invp",
            encode_action("ctrl.invp.InvpAction", invp.InvpAction(force=force)),
        )


def main() -> None:
    sut = PdPendulum("invp-sut")
    try:
        sut.spin()
    finally:
        sut.close()


if __name__ == "__main__":
    main()
