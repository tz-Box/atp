"""协议 data：各模块的观测/动作/结果 payload，随 import 注册到信封解码表。

算法侧与框架侧都只依赖本层（protocol），不依赖框架项目层（modules）。
"""
from . import manip, nav, slam  # noqa: F401  触发 register_data
