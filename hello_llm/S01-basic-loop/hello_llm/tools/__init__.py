"""工具层（包）—— Agent-Loop 的 execute 路径（论文图5 的 c 步骤）。

模块拆分：
    file_tools.py  文件工具实现（read_file / write_file / edit_file）
    registry.py    工具 Schema 池 + execute 分派

对外统一从本包导入：
    from hello_llm.tools import TOOLS, execute
"""

from .registry import TOOLS, execute

__all__ = ["TOOLS", "execute"]
