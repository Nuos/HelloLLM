"""python -m hello_llm 入口。

一、功能作用
    包模式的统一入口：委托给 entrypoints/cli.py 的 main()。
    对应运行方式：python -m hello_llm（与直接运行 entrypoints/cli.py 等价）。
缩略词说明（本模块涉及的术语）：
    1.  CLI —— Command-Line Interface，命令行接口
"""

import sys

from .entrypoints.cli import main

if __name__ == "__main__":
    sys.exit(main())
