"""模块：state/session.py —— 会话名生成与元数据。

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
        ├── __init__.py                   包入口（聚合导出）
        ├── store.py                      会话 JSONL 存储（对照 src/utils/sessionStorage.ts）
        └── session.py                    ★★★ 本模块：会话名生成与元数据 ★★★
====================================================================

缩略词说明（本模块涉及的术语）：
    1.  timestamp —— 时间戳：形如 20260803_143000 的时刻标记

本模块职责（业务含义）：
    会话没有用户命名时，需要一种"看一眼就知道什么时候聊的"默认名字；
    本模块负责生成这种时间戳会话名，并把"首条用户消息"提炼成
    可读标题（供 --list-sessions 展示时一眼认出是哪个会话）。
"""

from datetime import datetime  # 生成时间戳会话名


def generate_name(prefix: str = "") -> str:
    """函数：生成时间戳会话名。

    一、功能作用（算法）
        按"年月日_时分秒"生成唯一会话名（如 20260803_143000）；
        带 prefix 时追加在尾部（如 20260803_143000_重构），
        prefix 只保留合法字符（字母/数字/下划线/连字符），
        非法字符一律剔除——保证名字能安全拼进文件路径。

    二、输入（input）
        prefix：可选的会话主题前缀；不传则只有时间戳。

    三、输出（output）
        合法的会话名（符合 store.validate_name 的字符集）。
    """
    base = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not prefix:
        return base
    # 只保留 ASCII 字母/数字/下划线/连字符（isalnum 会把中文也算进去，
    # 而会话名校验只认 ASCII —— 这里必须与 store.validate_name 的字符集一致）
    cleaned = "".join(c for c in prefix if c.isascii() and (c.isalnum() or c in "_-"))[:40]
    return f"{base}_{cleaned}" if cleaned else base


def make_title(first_user_msg: str, limit: int = 20) -> str:
    """函数：把首条用户消息提炼成会话标题。

    一、功能作用
        取首条用户消息的前若干字符作为会话标题（换行折叠成空格），
        供会话列表展示——比时间戳名字更能让人认出会话内容。

    二、输入（input）
        first_user_msg：会话的第一条用户消息文本。
        limit：标题最大字符数（默认 20）。

    三、输出（output）
        提炼后的标题字符串；消息为空时返回"（空会话）"。
    """
    text = " ".join(first_user_msg.split())  # 折叠空白/换行
    if not text:
        return "（空会话）"
    return text[:limit] + ("…" if len(text) > limit else "")
