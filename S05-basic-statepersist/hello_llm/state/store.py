"""模块：state/store.py —— 会话持久化存储（JSONL 转录文件）。

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
        ├── store.py                      会话 JSONL 存储（对照 src/utils/sessionStorage.ts） ★★★ 本模块 ★★★
        └── session.py                    会话名生成与元数据
====================================================================

缩略词说明（本模块涉及的术语）：
    1.  JSONL —— JSON Lines：每行一条 JSON 记录的文件格式（追加日志型）
    2.  transcript —— 转录文件：会话逐条落盘的记录文件（对齐源码叫法）
    3.  meta —— metadata，元数据：描述会话本身的信息（创建时间/模型等）

本模块职责（业务含义）：
    把对话历史持久化到磁盘，让"关掉程序再打开"还能接着聊：
    每个会话一个 JSONL 转录文件（首行 meta 记录系统提示与模型，
    之后每行一条消息），支持追加写、整会话恢复、会话列表。
    对齐 claude-code 源码 utils/sessionStorage.ts 的设计：
    转录文件逐条追加（append），恢复时逐行回放（restore）。
"""

import json  # 消息序列化/反序列化（JSONL 每行一条）
import re  # 会话名校验（防路径注入）
import time  # 文件时间戳（列表展示用）
from datetime import datetime  # 会话元数据时间（可读格式）
from pathlib import Path  # 会话目录与文件路径
from typing import Optional  # 声明可选返回值

from ..utils import notice, tool, warn  # 项目日志层：调用时机可观测（调试用）

# 一、存储位置与上限
# 会话文件放在用户主目录的 .hellollm/sessions/ 下：
# 与 API 配置（~/.hellollm/config.json）同目录，项目无关、跨项目可用。
SESSIONS_DIR = Path.home() / ".hellollm" / "sessions"
# 转录文件读取上限（对齐源码 MAX_TRANSCRIPT_READ_BYTES = 50MB）：
# 超大会话文件只读前 50MB，防止一次恢复把内存撑爆。
MAX_TRANSCRIPT_READ_BYTES = 50 * 1024 * 1024
# 合法会话名字符集：字母/数字/下划线/连字符。
# 会话名会拼进文件路径，必须过滤掉 / . 等危险字符（防路径注入）。
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# 单行长度上限（16MB）：防单条超长消息（如巨型工具输出）写坏文件。
_MAX_LINE_BYTES = 16 * 1024 * 1024


def get_transcript_path(name: str) -> Path:
    """函数：计算会话转录文件的完整路径。

    一、功能作用
        把会话名映射成磁盘文件路径：<会话目录>/<会话名>.jsonl，
        并确保会话目录存在（不存在则创建）。

    二、输入（input）
        name：会话名（须通过 validate_name 校验，否则抛 ValueError）。

    三、输出（output）
        会话转录文件的绝对路径（Path 对象）。
    """
    validate_name(name)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR / f"{name}.jsonl"


def validate_name(name: str) -> None:
    """函数：校验会话名合法性。

    一、功能作用
        会话名会拼进文件路径，若含 / 或 .. 可被利用读写任意文件。
        只允许字母/数字/下划线/连字符（1-64 位），非法即抛异常，
        在生成路径前就把风险挡掉。

    二、输入（input）
        name：待校验的会话名。

    三、输出（output）
        无返回值；非法时抛 ValueError（附可用字符说明）。
    """
    if not _NAME_RE.match(name):
        raise ValueError(f"会话名不合法：{name!r}（只允许字母/数字/下划线/连字符，1-64 位）")


def create_session(name: str, system_prompt: str, model: str) -> Path:
    """函数：新建会话（写入 meta 首行）。

    一、功能作用（算法）
        在转录文件首行写一条 meta 记录（type=meta），记下系统提示、
        模型名与创建时间——恢复会话时用它重建 Conversation 的初始状态。
        已存在同名会话时抛异常（避免覆盖旧会话）。

    二、输入（input）
        name：新会话名（不能与已有会话重名）。
        system_prompt：会话的系统提示词（恢复时原样带回）。
        model：会话使用的模型名（记录用，展示给用户看）。

    三、输出（output）
        新会话转录文件的路径。
    """
    path = get_transcript_path(name)
    if path.exists():
        raise ValueError(f"会话已存在：{name}（请换一个名字，或用 --resume 恢复）")
    # 日志：会话创建时机（REPL --save-as 新会话 / headless --save-as 首次落盘）
    notice(f"会话创建：{name}（模型 {model}）")
    meta = {
        "type": "meta",
        "system_prompt": system_prompt,
        "model": model,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def append_message(name: str, message: dict) -> None:
    """函数：追加一条消息到会话转录文件。

    一、功能作用（算法）
        把单条消息（OpenAI 格式：role/content/tool_calls 等）序列化
        成 JSON 行追加到文件末尾（append 模式，对齐源码转录设计）。
        文件不存在（会话未创建）时自动按新建处理——先写 meta 再追加。

    二、输入（input）
        name：会话名。
        message：OpenAI 格式消息字典（role/content/tool_calls 等）。

    三、输出（output）
        无返回值；会话名非法抛 ValueError，消息序列化失败抛 JSONError。
    """
    path = get_transcript_path(name)
    if not path.exists():
        # 会话文件不存在：用占位 system 建一个，保证追加不丢数据
        create_session(name, "（自动创建）", "unknown")
    # 日志：消息落盘时机（REPL 每轮对话后 / headless 单轮结束后）
    tool(f"会话落盘：{name} 追加 {message.get('role', '?')} 消息")
    line = json.dumps(message, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_session(name: str) -> dict:
    """函数：从转录文件恢复整个会话。

    一、功能作用（算法）
        逐行回放转录文件（对齐源码 restoreSessionStateFromLog）：
        首行 meta 读出系统提示与模型；其余行按消息回放成列表。
        坏行（非法 JSON）跳过不中断——转录文件可能被半截写入；
        文件超 50MB 只读前部；文件不存在返回空会话（meta 用默认值）。

    二、输入（input）
        name：会话名。

    三、输出（output）
        会话状态字典：{"system_prompt", "model", "created_at", "messages"}；
        文件不存在时 messages 为空列表、其余字段用默认值。
    """
    path = get_transcript_path(name)
    state = {"system_prompt": "", "model": "", "created_at": "", "messages": []}
    if not path.exists():
        # 日志：恢复时机（会话文件不存在 → 视为新会话）
        notice(f"会话恢复：{name}（文件不存在，视为新会话）")
        return state
    size = path.stat().st_size
    if size > MAX_TRANSCRIPT_READ_BYTES:
        return {"system_prompt": "", "model": "", "created_at": "",
                "messages": [], "truncated": True}  # 超限：标记截断，不读
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if len(line) > _MAX_LINE_BYTES:
                continue  # 单行超限：跳过（防巨型坏行拖垮内存）
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # 坏行：跳过，不中断恢复
            if entry.get("type") == "meta":
                state["system_prompt"] = entry.get("system_prompt", "")
                state["model"] = entry.get("model", "")
                state["created_at"] = entry.get("created_at", "")
            else:
                state["messages"].append(entry)
    # 日志：恢复时机（会话文件存在 → 回放条数）
    notice(f"会话恢复：{name}（回放 {len(state['messages'])} 条消息）")
    return state


def list_sessions() -> list[dict]:
    """函数：列出所有会话及其概况。

    一、功能作用（算法）
        扫描会话目录下所有 .jsonl 文件，逐个读取文件大小、修改时间
        与消息行数，按修改时间倒序排列——供 --list-sessions 展示。

    二、输入（input）
        无。

    三、输出（output）
        会话概况列表（按最近修改倒序），每项含：
        会话名、消息数、文件大小、最后修改时间（可读格式）。
        目录不存在或为空时返回空列表。
    """
    if not SESSIONS_DIR.exists():
        return []
    # 日志：列表调用时机（--list-sessions 入口）
    sessions = []
    for path in sorted(SESSIONS_DIR.glob("*.jsonl")):
        name = path.stem
        count = 0
        try:
            for _ in path.open(encoding="utf-8"):
                count += 1
        except OSError:
            continue
        mtime = path.stat().st_mtime
        sessions.append({
            "name": name,
            "messages": max(count - 1, 0),  # 减掉 meta 行，剩消息数
            "size": path.stat().st_size,
            "updated": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
        })
    sessions.sort(key=lambda s: s["updated"], reverse=True)
    # 日志：列表结果（--list-sessions 展示后）
    notice(f"会话列表：共 {len(sessions)} 个会话")
    return sessions
