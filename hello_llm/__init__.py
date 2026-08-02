"""HelloLLM — 最简可交互 AI 编码 Agent（包入口）。

====================================================================
HelloLLM 项目框架结构（v1，论文图1 七组件模型 → 模块映射）

hello_llm/
├── __init__.py                 包入口：版本号 + 项目说明
├── __main__.py                 python -m hello_llm 入口
│
├── entrypoints/                一、交互表面层（图1 "Interfaces"）
│   ├── __init__.py
│   ├── cli.py                  CLI 入口：argparse + 配置校验 + 分派
│   ├── repl.py                 交互 REPL（多轮对话）
│   ├── headless.py             无头单次（对照 claude -p）
│   └── render.py               Agent-Loop 事件渲染
│
├── query/                      二、核心层（图1 "Agent Loop"）
│   ├── __init__.py
│   └── agent_loop.py           query_loop() 生成器 + Conversation
│
├── config/                     三、配置层（本地配置文件）
│   ├── __init__.py
│   └── loader.py               ~/.hellollm/config.json 定位/解析/合并
│
├── providers/                  四、模型提供商层（Agent-Loop 的 callModel）
│   ├── __init__.py
│   ├── config.py               ModelConfig 模型调用配置（含 API key 校验）
│   ├── types.py                数据结构与异常
│   ├── openai_compatible.py    stream_chat：SSE 流式客户端
│   └── client.py               consume_stream + call_model：流式事件聚合
│
├── tools/                      五、工具层（Agent-Loop 的 execute 路径）
    ├── __init__.py
    ├── registry.py             工具 Schema 池 + execute 分派
    └── file_tools.py           文件工具实现
│
└── logging/                    六、日志提示层（诊断提示/事件通知）
    ├── __init__.py
    └── events.py               事件提示函数（裁剪/预算/额度/工具）
====================================================================

v1 范围说明：
    依据论文《Dive into Claude Code》(arXiv:2604.14228v2) 图1 重建，
    仅含 CLI 入口层 + Agent-Loop 两个核心模块及其直接依赖；
    权限系统 / MCP / 记忆 / 压缩 / 持久化 / Hook / 子 Agent
    留待后续版本按论文逐步添加。

缩略词术语表（本项目出现即按此解释）：
    1.  CLI —— Command-Line Interface，命令行接口
    2.  REPL —— Read-Eval-Print Loop，读取-求值-打印循环（交互式对话界面）
    3.  Agent —— 智能体（能自主调用工具完成任务循环的程序）
    4.  Agent-Loop —— 智能体循环（模型调用/工具分派/结果收集的迭代周期）
    5.  API —— Application Programming Interface，应用程序编程接口
    6.  SSE —— Server-Sent Events，服务器推送事件（HTTP 流式协议，逐块推送）
    7.  JSON —— JavaScript Object Notation，轻量数据交换格式
    8.  Schema —— 结构定义；工具参数 Schema = 参数结构约束（JSON Schema 子集）
    9.  HTTP —— HyperText Transfer Protocol，超文本传输协议
    10. Bearer —— HTTP 鉴权方案（Authorization: Bearer <token>）
    11. TTY —— Teletype，终端设备；isatty() = 判断 stdin 是否为交互终端
    12. ANSI —— 转义序列：终端颜色/样式控制码（如 \\033[2m 暗淡字体）
    13. DNS —— Domain Name System，域名系统
    14. URL —— Uniform Resource Locator，统一资源定位符
    15. ReAct —— Reasoning + Acting：推理与行动交替的智能体模式（论文 §4.1）
    16. ID —— Identifier，标识符
    17. UTF-8 —— 8-bit Unicode Transformation Format，可变长字符编码
    18. EOF —— End of File，文件结束（终端 Ctrl-D 触发 EOFError）
    19. MCP —— Model Context Protocol，模型上下文协议（论文 §6 扩展机制，后续版本引入）
    20. F5 —— VS Code 调试启动快捷键（指代调试入口；不加载 shell 配置文件）
"""

__version__ = "0.1.0"
