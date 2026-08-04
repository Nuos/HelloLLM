"""模块：tools/file_tools.py —— 文件工具实现（read_file / write_file / edit_file）。

====================================================================
HelloLLM 项目框架结构（S06-basic-ee，论文图1 七组件模型 → 模块映射）

S06-basic-ee/
└── hello_llm/                            # Python 包
    ├── __init__.py                       包入口：版本号 + 项目说明 + 全局术语表
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
    │   └── file_tools.py                 read_file / write_file / edit_file 实现 ★★★ 本模块 ★★★
    │
    └── utils/                            六、工具函数层（对照 src/utils/：config.ts 等）
        ├── __init__.py
        ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）
        └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）缩略词说明（本模块涉及的术语）：
    1.  NUL —— 空字节（\x00）：二进制文件的典型特征标记
"""

from __future__ import annotations

from pathlib import Path


MAX_READ_BYTES = 200_000


def read_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"错误：文件不存在：{path}"
    if p.is_dir():
        return f"错误：{path} 是目录，无法读取"
    data = p.read_bytes()
    if b"\x00" in data[:8192]:
        return f"错误：{path} 是二进制文件，不支持读取"
    text = data.decode("utf-8", "replace")
    if len(data) > MAX_READ_BYTES:
        text = text[:MAX_READ_BYTES] + "\n…（文件过大，已截断）"
    return text


def write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {len(content.encode('utf-8'))} 字节到 {path}"


def edit_file(path: str, old_string: str, new_string: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"错误：文件不存在：{path}"
    text = p.read_text(encoding="utf-8")
    if old_string not in text:
        return f"错误：未在 {path} 中找到要替换的文本，请核对 old_string 与文件内容完全一致"
    n = text.count(old_string)
    text = text.replace(old_string, new_string, 1)
    p.write_text(text, encoding="utf-8")
    return f"已在 {path} 中替换第 1/{n} 处匹配"
