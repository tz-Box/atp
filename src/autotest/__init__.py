"""autotest —— 统一自动化测试服务框架。"""

def _version() -> str:
    """包版本（单一事实源：pyproject.toml；未安装环境退化为 dev）。

    此前这里写死一份 `"0.1.0"`，pyproject 与 http.py 又各存一份——三份会漂，
    而**漂了不会报错**：三处都长得像个合法版本号。2026-09-12 收敛到一处。
    """
    import importlib.metadata
    try:
        return importlib.metadata.version("tz_atp")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


__version__ = _version()

# 已实现到哪一版《PatrolBox 通信与接口总契约》（v1.7-R12，四方统一口径）。
# 与包版本 __version__ 是两回事：前者答"我实现的是哪版跨平台契约"，后者答"我是哪个发行版"。
# 纪律（R12）：实现某版本的全部己方条款后，才允许把本常量提上去。
# ATP 己方条款清单见 docs/scheme/ATP推进计划-2026-09.md §2。
CONTRACT_VERSION = "1.7"
