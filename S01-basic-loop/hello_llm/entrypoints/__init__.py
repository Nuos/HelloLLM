"""交互表面层（包）—— 论文图1 "Interfaces" / 论文 §3.3 交互表面层。

对应参考实现（claude-code 源码 src/entrypoints/）：
    本包 = 所有入口与界面代码，套同一个 Agent-Loop（query/agent_loop.py）。

模块清单：
    cli.py       CLI 入口：argparse + 配置校验 + 分派（薄壳）
    repl.py      交互式 REPL（多轮对话）
    headless.py  无头单次（对照 claude -p）
    render.py    Agent-Loop 事件渲染

缩略词说明（本模块涉及的术语）：
    1. CLI —— Command-Line Interface，命令行接口
    2. REPL —— Read-Eval-Print Loop，读取-求值-打印循环（交互式对话界面）
"""
