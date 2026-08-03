"""模块：services/api/types.py —— 模型提供商层的数据结构与异常。

====================================================================
HelloLLM 项目框架结构（S03-basic-hook，论文图1 七组件模型）

S03-basic-hook/
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
    │       ├── types.py                  数据结构与异常 ★★★ 本模块 ★★★
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
    └── hooks/                            七、hook 机制（★ S03 新增，对照 src/utils/hooks.ts）
        ├── __init__.py                   包入口（聚合导出）
        ├── config.py                     加载 hook 规则（项目内 + 用户级合并）
        ├── runner.py                     subprocess 执行 hook（stdin/stdout JSON）
        └── manager.py                    HookManager：PreToolUse/PostToolUse 调度
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
        由 services/api/claude.py 抛出，entrypoints/render.py 捕获
        并转为中文提示，不让用户看到裸 traceback。

    二、kind 分类（自动重试依据）
        http       —— HTTP 错误（400/401/429…），重试无益，不自动重试
        network    —— 网络层错误（连接重置/拒绝/DNS），可自动重试
        timeout    —— 请求/读取超时，可自动重试
        unknown    —— 其他（默认）
    """

    def __init__(self, message: str, kind: str = "unknown"):
        """方法：初始化异常（message 提示文本；kind 错误分类）。

        一、功能作用
            扩展 RuntimeError：携带 kind 分类，供 agent_loop 的
            自动重试逻辑判断（network/timeout 才重试）。
        """
        super().__init__(message)
        self.kind = kind  # kind：错误分类（http/network/timeout/unknown）


class ConfigError(RuntimeError):
    """异常：配置错误（如 API key 缺失）。

    一、功能作用
        由 providers/config.py 的 require_api_key() 抛出，
        entrypoints/cli.py 捕获并 fail-fast 退出（退出码 1），
        打印完整配置指引。
    """
