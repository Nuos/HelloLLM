"""HelloLLM — 最简可交互 AI 编码 Agent（包入口）。

====================================================================
HelloLLM 项目框架结构（S05-basic-statepersist，论文图1 七组件模型）

S05-basic-statepersist/
└── hello_llm/                            # Python 包
    ├── __init__.py                       包入口：版本号 + 项目说明 + 全局术语表 ★★★ 本模块 ★★★
    ├── __main__.py                       python -m hello_llm 入口
    │
    ├── entrypoints/                      一、交互表面层（图1 "Interfaces"，对照 src/entrypoints/）
    │   ├── cli.py                        CLI 入口（对照 src/entrypoints/cli.tsx）
    │   ├── repl.py                       交互 REPL（多轮对话）
    │   ├── headless.py                   无头单次（对照 claude -p）
    │   └── render.py                     Agent-Loop 事件渲染
    │
    ├── query/                            二、核心层（图1 "Agent Loop"，对照 src/query/）
    │   ├── __init__.py
    │   └── agent_loop.py                 query_loop() 生成器 + Conversation（对照 queryLoop）
    │
    ├── services/                         三、服务层（对照 src/services/）
    │   └── api/                          四、API 客户端（对照 src/services/api/）
    │       ├── __init__.py
    │       ├── config.py                 ModelConfig 模型调用配置（含 API key 校验）
    │       ├── types.py                  数据结构与异常
    │       ├── claude.py                 stream_chat：SSE 流式客户端（对照 claude.ts）
    │       └── client.py                 consume_stream + call_model（对照 client.ts）
    │
    ├── tools/                            五、工具层（对照 src/tools/：FileReadTool 等）
    │   ├── __init__.py
    │   ├── registry.py                   工具 Schema 池 + execute 分派（对照 tools.ts）
    │   └── file_tools.py                 read_file / write_file / edit_file 实现
    │
    ├── utils/                            六、工具函数层（对照 src/utils/：config.ts 等）
    │   ├── __init__.py
    │   ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）
    │   └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）
    │
    ├── hooks/                            七、hook 机制（★ S03 新增，对照 src/utils/hooks.ts）
        ├── __init__.py                   包入口（聚合导出）
        ├── config.py                     加载 hook 规则（项目内 + 用户级合并）
        ├── runner.py                     subprocess 执行 hook（stdin/stdout JSON）
        ├── manager.py                    HookManager：PreToolUse/PostToolUse 调度
    │
    └── state/                            八、状态与持久化（★ S05 新增，图1 "State & Persistence"）
        ├── __init__.py                   包入口（聚合导出）
        ├── store.py                      会话 JSONL 存储（对照 src/utils/sessionStorage.ts）
        └── session.py                    会话名生成与元数据
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
