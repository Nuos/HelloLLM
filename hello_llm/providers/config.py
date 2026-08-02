"""模块：providers/config.py —— 模型调用配置 ModelConfig。

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
│   ├── config.py               ★★★ 本模块：ModelConfig 模型调用配置 ★★★
│   ├── types.py                数据结构与异常
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
    1.  API —— Application Programming Interface，应用程序编程接口
    2.  JSON —— JavaScript Object Notation，轻量数据交换格式（配置文件格式）
    3.  F5 —— VS Code 调试启动快捷键（指代调试入口；不加载 shell 配置）
"""

from __future__ import annotations  # 延迟求值注解

from dataclasses import dataclass, field  # dataclass：轻量配置容器
from typing import Optional  # 类型标注：可选字段

from .types import ConfigError  # 配置错误异常（API key 缺失等）

# 一、内置默认值（仅当命令行与配置文件都未设置时生效）
DEFAULT_API_BASE = "https://api.deepseek.com"  # 默认端点：DeepSeek OpenAI 兼容接口
DEFAULT_MODEL = "deepseek-v4-flash"  # 默认模型：推理模型（支持工具调用）

# 二、默认系统提示词：定义 Agent 身份与工具使用准则，注入每条 system 消息
DEFAULT_SYSTEM = (
    "You are HelloLLM, a minimal coding agent rebuilt from the architecture of "
    "Claude Code (paper: arXiv:2604.14228). You can read, write and edit files "
    "with tools. Be concise. When a task involves files, use the tools."
)

# 三、配置文件默认路径（错误提示用；实际定位逻辑在 config/loader.py）
DEFAULT_CONFIG_PATH = "~/.hellollm/config.json"


@dataclass
class ModelConfig:
    """数据结构：模型调用配置。

    一、功能作用
        聚合一次模型调用所需的全部参数（端点/密钥/模型/超时），
        由 config/loader.py 按"命令行 > 配置文件 > 默认值"合并后构建，
        供 providers/openai_compatible.py 发起请求使用。

    二、字段说明
        api_base      （str）OpenAI 兼容端点，如 https://api.deepseek.com
        api_key       （str）鉴权密钥，请求头 Authorization: Bearer <key>
        model         （str）模型名，如 deepseek-v4-flash / deepseek-v4-pro
        timeout       （float|None）HTTP 超时秒数；None=由配置文件/默认值决定
        max_tokens    （int|None）单次输出 token 上限；None 交给服务端默认
        file_config   （dict）配置文件内容（由 config/loader.py 注入；
                      repr=False 防止打印配置时泄露 api_key）

    三、配置优先级
        1. 命令行显式参数（--model / --api-base / --api-key / --timeout）
        2. 本地配置文件（~/.hellollm/config.json 或 --config <path>）
        3. 内置默认值（DEFAULT_* 常量）

    四、明确不使用的来源
        环境变量 —— VS Code F5 调试不加载 shell 配置文件，环境变量方案在
        调试场景不可靠。API key 的本地来源只有配置文件（或命令行 --api-key）。
    """

    api_base: str = ""  # 端点：OpenAI 兼容 API 地址
    api_key: str = ""  # 密钥：Bearer 鉴权令牌
    model: str = ""  # 模型名
    timeout: Optional[float] = None  # 超时：秒；None=未指定
    max_tokens: Optional[int] = None  # 输出上限：token 数；None=服务端默认
    file_config: dict = field(default_factory=dict, repr=False)  # 配置文件内容

    def __post_init__(self) -> None:
        """初始化后处理：字段回退（命令行显式值 → 配置文件 → 内置默认值）。"""
        fc = self.file_config or {}  # fc：配置文件内容字典（缩写）
        self.api_base = (
            self.api_base or fc.get("api_base") or DEFAULT_API_BASE
        ).rstrip("/")  # 去尾部斜杠：避免拼 URL 时出现双斜杠
        self.api_key = self.api_key or fc.get("api_key") or ""
        self.model = self.model or fc.get("model") or DEFAULT_MODEL
        timeout = self.timeout  # timeout：命令行/构造时显式值
        if timeout is None:
            timeout = fc.get("timeout")  # 回退：配置文件里的超时
        self.timeout = float(timeout) if timeout is not None else 120.0

    def require_api_key(self) -> None:
        """方法：校验 API key 已配置；缺失时抛 ConfigError（fail-fast）。

        一、功能作用
            发起网络请求前必须确认 key 已配置，否则服务端必然 401 ——
            fail-fast 在本地报错，比请求发出后再报错更快、更友好。
            指引指向本地配置文件（含格式示例与创建命令）。

        二、异常
            ConfigError：api_key 为空时抛出，含完整配置指引。
        """
        if not self.api_key:
            raise ConfigError(
                "未配置 API Key。\n"
                f"  推荐：在本地配置文件 {DEFAULT_CONFIG_PATH} 中填写（JSON 格式）：\n"
                '    {\n'
                '      "api_key": "sk-...",\n'
                '      "api_base": "https://api.deepseek.com",\n'
                '      "model": "deepseek-v4-flash"\n'
                '    }\n'
                f"  创建方式：mkdir -p ~/.hellollm && 写入上述内容 && chmod 600 {DEFAULT_CONFIG_PATH}\n"
                "  备选：命令行 --api-key sk-...（临时用）或 --config <path>（指定其他配置文件）"
            )
