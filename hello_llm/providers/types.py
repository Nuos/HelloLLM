"""模块：providers/types.py —— 模型提供商层的数据结构与异常。

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
│   ├── types.py                ★★★ 本模块：数据结构与异常 ★★★
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
缩略词说明（本模块涉及的术语）：
    1.  ID —— Identifier，标识符
"""

from __future__ import annotations  # 延迟求值注解：允许前向引用、注解保持字符串

from dataclasses import dataclass, field  # dataclass：轻量数据容器（结构体）
from typing import Any  # 类型标注：参数字典的通用值


@dataclass
class ToolCall:
    """数据结构：一次工具调用（模型输出的行动请求）。

    一、功能作用
        承载模型请求的单个工具调用 {name, arguments}，
        由 query/agent_loop.py 分派给 tools/registry.py 执行。

    二、字段说明
        id         （str）工具调用 ID：tool 角色消息用 tool_call_id 与之关联
        name       （str）工具名（read_file / write_file / edit_file ...）
        arguments  （dict）已解析为字典的参数（原始 JSON 字符串已 json.loads）
    """

    id: str  # ID：工具调用标识（OpenAI 协议要求回填时一一对应）
    name: str  # 工具名：execute 分派依据（查 tools/registry.py 的 _IMPL 表）
    arguments: dict[str, Any]  # 参数字典：解包为关键字参数传给工具实现函数


@dataclass
class ModelResponse:
    """数据结构：一次模型调用的完整结果（流式事件聚合产物）。

    一、功能作用
        作为 Agent-Loop 一轮迭代的输出：
            有 tool_calls → 走 execute 路径；
            只有 text    → 触发停止条件（论文 §4.5）。

    二、字段说明
        text        （str）模型生成的正文（纯文本回复时即最终答案）
        tool_calls  （list）模型请求的工具调用列表
    """

    text: str = ""  # 正文：模型生成的文本内容
    tool_calls: list[ToolCall] = field(default_factory=list)  # 工具调用列表

    @property
    def has_tool_calls(self) -> bool:
        """属性：是否含工具调用 —— Agent-Loop 停止条件判断依据（§4.5）。"""
        return bool(self.tool_calls)


class ModelError(RuntimeError):
    """异常：模型调用失败（HTTP 错误 / 网络错误 / 超时）。

    一、功能作用
        由 providers/openai_compatible.py 抛出，entrypoints/render.py 捕获
        并转为中文提示，不让用户看到裸 traceback。
    """


class ConfigError(RuntimeError):
    """异常：配置错误（如 API key 缺失）。

    一、功能作用
        由 providers/config.py 的 require_api_key() 抛出，
        entrypoints/cli.py 捕获并 fail-fast 退出（退出码 1），
        打印完整配置指引。
    """
