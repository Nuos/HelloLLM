"""模块：tools/registry.py —— 工具注册表与 execute 分派。

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
    ├── registry.py             ★★★ 本模块：工具 Schema 池 + execute 分派 ★★★
    └── file_tools.py           文件工具实现
│
└── logging/                    六、日志提示层（诊断提示/事件通知）
    ├── __init__.py
    └── events.py               事件提示函数（裁剪/预算/额度/工具）
====================================================================
缩略词说明（本模块涉及的术语）：
    1.  Schema —— 结构定义；工具参数 Schema = 参数结构约束（JSON Schema 子集）
    2.  API —— Application Programming Interface，应用程序编程接口
"""

from __future__ import annotations  # 延迟求值注解

from typing import Any, Callable  # Any：参数字典值；Callable：实现函数签名

from .file_tools import read_file, write_file, edit_file  # 文件工具实现

# 一、工具 Schema 池（OpenAI function calling 格式）。
#     随请求体发给模型，模型据此决定"何时调用、传什么参数"
#     （论文 §6.2 工具池组装）。描述写清楚"何时用"，模型靠 description 选工具。
TOOLS: list[dict] = [  # TOOLS：工具 Schema 列表
    {
        "type": "function",  # 工具类型：function（OpenAI 协议固定值）
        "function": {
            "name": "read_file",  # 工具名：模型调用时使用
            "description": "读取文本文件内容。任何需要读取文件的任务都用它。",
            "parameters": {  # 参数 Schema：JSON Schema 子集
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要读取的文件路径（相对或绝对）"},
                },
                "required": ["path"],  # 必填参数：模型漏传时 JSON schema 校验兜底
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入（覆盖）文件；父目录不存在时自动创建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "完整文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "在文件中用 new_string 替换第一处出现的 old_string。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
]

# 二、工具名 → 实现函数 的映射表（execute 的分派依据）。
#     Schema 与实现分离：Schema 给模型看（API 协议），实现给 execute 调用（本地执行）。
_IMPL: dict[str, Callable[..., str]] = {  # _IMPL：工具实现映射表
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
}


def execute(name: str, arguments: dict[str, Any]) -> str:
    """函数：执行工具调用，返回 tool_result 文本（论文图5 的 c 步骤）。

    一、功能作用
        收到 query/agent_loop.py 的 tool_use 请求后，按工具名查 _IMPL 表
        执行，结果作为 tool_result 文本回填历史。

    二、参数
        name       （str）工具名（模型请求的）
        arguments  （dict）参数字典（解包为关键字参数传给实现函数）

    三、返回
        str：成功信息或错误信息（都作为 tool_result 回给模型）。

    四、设计原则
        1. 任何异常都被捕获并转为错误文本 —— 工具失败不崩循环，
           错误作为上下文回给模型（ReAct 模式核心）；
        2. 未知工具名返回错误文本 —— 模型偶发幻觉工具名时的兜底。
    """
    fn = _IMPL.get(name)  # fn：查表得到的实现函数
    if fn is None:
        return f"错误：未知工具 {name}"
    try:
        return fn(**arguments)  # 参数字典解包为关键字参数
    except Exception as e:  # 参数缺失（TypeError）/ 权限（PermissionError）等
        return f"错误：工具 {name} 执行失败：{e}"
