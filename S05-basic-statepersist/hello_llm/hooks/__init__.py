"""模块：hooks —— hook 机制（包），S03 新增。

====================================================================
HelloLLM 项目框架结构（S05-basic-statepersist，论文图1 七组件模型）

S05-basic-statepersist/
└── hello_llm/
    ├── __init__.py                       包入口：版本号 + 项目说明 + 全局术语表
    ├── __main__.py                       python -m hello_llm 入口
    │
    ├── entrypoints/                      一、交互表面层（图1 "Interfaces"）
    │   ├── cli.py                        CLI 入口（对照 src/entrypoints/cli.tsx）
    │   ├── repl.py                       交互 REPL（多轮对话）
    │   ├── headless.py                   无头单次（对照 claude -p）
    │   └── render.py                     Agent-Loop 事件渲染
    │
    ├── query/                            二、核心层（图1 "Agent Loop"）
    │   └── agent_loop.py                 query_loop() 生成器 + Conversation
    │
    ├── services/                         三、服务层（对照 src/services/）
    │   └── api/                          四、API 客户端（对照 src/services/api/）
    │
    ├── tools/                            五、工具层（对照 src/tools/）
    │
    ├── utils/                            六、工具函数层（对照 src/utils/）
    │
    ├── hooks/                            七、hook 机制（★ S03 新增，对照 src/utils/hooks.ts）
        ├── __init__.py                   ★★★ 本模块：包入口（聚合导出）★★★
        ├── config.py                     加载 hook 规则（项目内 + 用户级合并）
        ├── runner.py                     subprocess 执行 hook（stdin/stdout JSON）
        └── manager.py                    HookManager：PreToolUse/PostToolUse 调度
====================================================================
    │
    └── state/                            八、状态与持久化（★ S05 新增，图1 "State & Persistence"）
        ├── __init__.py                   包入口（聚合导出）
        ├── store.py                      会话 JSONL 存储（对照 src/utils/sessionStorage.ts）
        └── session.py                    会话名生成与元数据

缩略词说明（本模块涉及的术语）：
    1.  hook —— 钩子：外部命令在工具执行前/后触发的扩展点
    2.  PreToolUse —— 工具执行前 hook（拦截/改写输入）
    3.  PostToolUse —— 工具执行后 hook（改写输出）
    4.  fail-open —— 失败放行：hook 异常时不阻断主流程
"""

from .config import load_hook_rules, match_hook  # 规则表加载、规则命中判断
from .runner import run_hook_command, build_payload, HOOK_TIMEOUT  # 外部命令执行、上下文拼装、超时上限
from .manager import HookManager  # 工具管理中枢（工具执行前后跑 hook）

__all__ = [
    "load_hook_rules",
    "match_hook",
    "run_hook_command",
    "build_payload",
    "HOOK_TIMEOUT",
    "HookManager",
]
