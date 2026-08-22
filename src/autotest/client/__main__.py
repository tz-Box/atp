"""`python -m autotest.client` 启动 CLI 控制端。"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())  # 返回码即退出码（0 成功 / 1 评测失败 / 2 用法错误），CI 依赖
