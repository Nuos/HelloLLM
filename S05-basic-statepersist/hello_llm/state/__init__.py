"""模块：state —— 状态与持久化（包），S05 新增。

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
    ├── hooks/                            七、hook 机制（对照 src/utils/hooks.ts，S03）
    │
    └── state/                            八、状态与持久化（★ S05 新增，图1 "State & Persistence"）
        ├── __init__.py                   ★★★ 本模块：包入口（聚合导出）★★★
        ├── store.py                      会话 JSONL 存储（对照 src/utils/sessionStorage.ts）
        └── session.py                    会话名生成与元数据
====================================================================

缩略词说明（本模块涉及的术语）：
    1.  JSONL —— JSON Lines：每行一条 JSON 记录的文件格式（追加日志型）
    2.  transcript —— 转录文件：会话逐条落盘的记录文件（对齐源码叫法）
    3.  meta —— metadata，元数据：描述会话本身的信息

本模块职责（业务含义）：
    论文图1 "State & Persistence"（状态与持久化）的最小实现：
    对话历史逐条追加进 JSONL 转录文件（对齐 claude-code 源码
    utils/sessionStorage.ts 的 transcript 设计），支持恢复继续对话
    与列出历史会话。
"""

from .store import (  # 会话存储（JSONL 转录文件）
    get_transcript_path,  # 会话文件路径计算
    validate_name,  # 会话名合法性校验
    create_session,  # 新建会话（写 meta 首行）
    append_message,  # 追加一条消息
    load_session,  # 恢复整个会话
    list_sessions,  # 列出所有会话
    MAX_TRANSCRIPT_READ_BYTES,  # 转录文件读取上限（对齐源码 50MB）
)
from .session import generate_name, make_title  # 会话名生成 / 标题提炼

__all__ = [
    "get_transcript_path",
    "validate_name",
    "create_session",
    "append_message",
    "load_session",
    "list_sessions",
    "MAX_TRANSCRIPT_READ_BYTES",
    "generate_name",
    "make_title",
]
