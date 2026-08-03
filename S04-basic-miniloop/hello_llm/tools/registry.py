"""模块：tools/registry.py —— 工具注册表与 execute 分派。

====================================================================
HelloLLM 项目框架结构（S04-basic-miniloop，论文图1 七组件模型）

S04-basic-miniloop/
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
    │   ├── registry.py                   工具 Schema 池 + execute 分派（对照 tools.ts）★★★ 本模块 ★★★
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
        └── manager.py                    HookManager：PreToolUse/PostToolUse 调度
    │
    └── permissions/                      八、权限系统（★ S04 新增，图1 "Permission System"）
        ├── __init__.py                   包入口（聚合导出）
        ├── policies.py                   工具→策略等级映射（deny-first）
        ├── modes.py                      权限模式（interactive / auto-accept / read-only）
        └── gate.py                       PermissionGate：execute 前检查（allow/ask/deny）
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
    """函数：工具执行分派。

一、功能作用（算法）
    按工具名查注册表：命中则调用对应实现函数并返回结果文本；
    未命中（未知工具）返回明确错误文本；实现函数抛异常时捕获并
    转为错误文本——保证工具执行永不向上抛异常，错误以工具结果
    形式回填给模型。

二、输入（input）
    name：工具名（如 read_file）。
    arguments：工具参数（路径/内容等），按工具实现取用。

三、输出（output）
    工具结果文本（成功摘要或错误说明），作为 tool_result 回填给模型。    """
    fn = _IMPL.get(name)  # fn：查表得到的实现函数
    if fn is None:
        return f"错误：未知工具 {name}"
    try:
        return fn(**arguments)  # 参数字典解包为关键字参数
    except Exception as e:  # 参数缺失（TypeError）/ 权限（PermissionError）等
        return f"错误：工具 {name} 执行失败：{e}"
