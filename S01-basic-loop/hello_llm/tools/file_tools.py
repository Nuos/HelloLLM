"""模块：tools/file_tools.py —— 文件工具实现（read_file / write_file / edit_file）。

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
    └── file_tools.py           ★★★ 本模块：文件工具实现 ★★★
│
└── logging/                    六、日志提示层（诊断提示/事件通知）
    ├── __init__.py
    └── events.py               事件提示函数（裁剪/预算/额度/工具）
====================================================================
缩略词说明（本模块涉及的术语）：
    1.  NUL —— 空字节（\x00）：二进制文件的典型特征标记
"""

from __future__ import annotations  # 延迟求值注解

from pathlib import Path  # 跨平台路径操作（展开 ~、创建父目录、读写文件）

# 一、模块常量
MAX_READ_BYTES = 200_000  # 单文件读取上限：防止一次读入超大文件撑爆上下文（论文 §3.6 每工具结果预算）


def read_file(path: str) -> str:
    """函数：读取文本文件内容（read_file 工具实现）。

    一、功能作用
        任何需要读取文件的任务都由模型调用本函数，返回文件内容文本。

    二、参数
        path  （str）要读取的文件路径（相对或绝对，支持 ~ 展开）

    三、返回
        str：文件内容；异常情形返回错误文本而非抛异常。

    四、安全策略（拒绝情形）
        1. 文件不存在 → 错误文本
        2. 是目录 → 错误文本
        3. 二进制（前 8KB 含 NUL 字节）→ 错误文本
        4. 超大文件（>MAX_READ_BYTES）→ 截断并标注
        错误作为 tool_result 回给模型，让模型自行调整 —— 这是 ReAct
        循环的关键行为：工具失败不是致命错误，而是下一条上下文。
    """
    p = Path(path).expanduser()  # p：展开 ~ 后的路径对象
    if not p.exists():
        return f"错误：文件不存在：{path}"
    if p.is_dir():
        return f"错误：{path} 是目录，无法读取"
    data = p.read_bytes()  # data：原始字节（先按字节读，便于二进制检测）
    if b"\x00" in data[:8192]:  # NUL 字节是二进制文件的典型特征
        return f"错误：{path} 是二进制文件，不支持读取"
    text = data.decode("utf-8", "replace")  # text：解码后的文本（失败字符用替换符）
    if len(data) > MAX_READ_BYTES:
        text = text[:MAX_READ_BYTES] + "\n…（文件过大，已截断）"  # 截断并标注
    return text


def write_file(path: str, content: str) -> str:
    """函数：写入（覆盖）文件，父目录自动创建（write_file 工具实现）。

    一、功能作用
        创建/覆盖文件；深目录无需预先创建。

    二、参数
        path     （str）文件路径
        content  （str）完整文件内容

    三、返回
        str：写入确认（含字节数，模型可据此核对）。
    """
    p = Path(path).expanduser()  # p：展开 ~ 后的路径对象
    p.parent.mkdir(parents=True, exist_ok=True)  # 父目录：不存在则递归创建
    p.write_text(content, encoding="utf-8")
    return f"已写入 {len(content.encode('utf-8'))} 字节到 {path}"


def edit_file(path: str, old_string: str, new_string: str) -> str:
    """函数：用 new_string 替换文件中第一处 old_string（edit_file 工具实现）。

    一、功能作用
        定点修改文件内容 —— 模型先 read_file 看内容，再 edit_file 改局部，
        无需重写整个文件。

    二、参数
        path        （str）文件路径
        old_string  （str）要查找的原文（精确匹配）
        new_string  （str）替换后的新文

    三、返回
        str：替换确认（含共 N 处匹配、已替换第 1 处）。

    四、语义（对齐 Claude Code 的 Edit 工具）
        1. 精确匹配（不模糊、不正则），保证可预期；
        2. 只替换第一处，并回报共 N 处匹配，让模型知道后续是否还需处理；
        3. 未命中时明确提示"完全一致"，引导模型回读文件核对
           （这是最常见失败原因）。
    """
    p = Path(path).expanduser()  # p：展开 ~ 后的路径对象
    if not p.exists():
        return f"错误：文件不存在：{path}"
    text = p.read_text(encoding="utf-8")  # text：当前文件内容
    if old_string not in text:
        return f"错误：未在 {path} 中找到要替换的文本，请核对 old_string 与文件内容完全一致"
    n = text.count(old_string)  # n：匹配总数（供模型判断剩余工作）
    text = text.replace(old_string, new_string, 1)  # 只替换第一处
    p.write_text(text, encoding="utf-8")
    return f"已在 {path} 中替换第 1/{n} 处匹配"
