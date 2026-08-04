"""模块：utils/config.py —— 本地配置文件加载。

====================================================================
HelloLLM 项目框架结构（S05-basic-statepersist，论文图1 七组件模型）

S05-basic-statepersist/
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
    │   └── file_tools.py                 read_file / write_file / edit_file 实现
    │
    ├── utils/                            六、工具函数层（对照 src/utils/：config.ts 等）
    │   ├── __init__.py
    │   ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）★★★ 本模块 ★★★
    │   └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）
    │
    ├── hooks/                            七、hook 机制（★ S03 新增，对照 src/utils/hooks.ts）
        ├── __init__.py                   包入口（聚合导出）
        ├── config.py                     加载 hook 规则（项目内 + 用户级合并）
        ├── runner.py                     subprocess 执行 hook（stdin/stdout JSON）
        └── manager.py                    HookManager：PreToolUse/PostToolUse 调度
    │
    └── state/                            八、状态与持久化（★ S05 新增，图1 "State & Persistence"）
        ├── __init__.py                   包入口（聚合导出）
        ├── store.py                      会话 JSONL 存储（对照 src/utils/sessionStorage.ts）
        └── session.py                    会话名生成与元数据

    1.  API —— Application Programming Interface，应用程序编程接口
    2.  JSON —— JavaScript Object Notation，轻量数据交换格式（配置文件格式）
"""

from __future__ import annotations  # 延迟求值注解

import json  # 解析配置文件 JSON
from pathlib import Path  # 跨平台路径操作
from typing import Optional  # 类型标注

from ..services.api.config import ModelConfig  # 模型调用配置（合并目标）

# 一、默认配置目录与文件（用户级，含 API key 等敏感信息）
DEFAULT_CONFIG_DIR = Path.home() / ".hellollm"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"


def find_config_path(explicit: Optional[str] = None) -> Optional[Path]:
    """函数：定位配置文件；不存在时返回 None（不报错，由 require_api_key 校验兜底）。

    一、功能作用
        确定配置文件路径，供 load_config 读取。

    二、参数
        explicit  （str|None）--config 显式指定的路径

    三、语义
        1. --config <path> 显式指定 → 只认该路径（不存在即视为无配置，
           不回退默认路径 —— 显式指定表达了用户的明确意图）；
        2. 未指定 → 默认路径 ~/.hellollm/config.json。

    四、返回
        Path|None：存在的配置文件路径；找不到返回 None。
    """
    if explicit:
        p = Path(explicit).expanduser()  # p：展开 ~ 后的显式路径
        return p if p.is_file() else None
    return DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.is_file() else None


def load_config(explicit: Optional[str] = None) -> dict:
    """函数：读取配置文件为字典；文件缺失/解析失败时返回 {}（不抛异常）。

    一、功能作用
        把配置文件 JSON 读成字典，供 build_model_config 合并。

    二、参数
        explicit  （str|None）--config 显式指定的路径

    三、配置缺失处理
        缺失/损坏不在这里报错 —— 统一由 ModelConfig.require_api_key()
        在校验层 fail-fast，给出完整配置指引（含文件路径与格式）。
    """
    p = find_config_path(explicit)  # p：定位到的配置文件路径
    if p is None:
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))  # data：解析后的配置字典
        return data if isinstance(data, dict) else {}  # 顶层必须是 JSON 对象
    except (OSError, json.JSONDecodeError):
        return {}  # 读失败按"无配置"处理，走 key 校验兜底


def build_model_config(args) -> ModelConfig:
    """函数：合并命令行参数与配置文件，构建 ModelConfig。

    一、功能作用
        按"命令行显式参数 > 配置文件 > 内置默认值"的优先级合并，
        是 CLI 入口获取最终配置的唯一通道。

    二、参数
        args  （argparse.Namespace）命令行参数
              （含 config / api_base / api_key / model / timeout 字段）

    三、返回
        ModelConfig：命令行未传的字段由配置文件回填；配置文件也没有则用默认值。
    """
    file_config = load_config(getattr(args, "config", None))  # file_config：配置文件内容
    return ModelConfig(
        api_base=args.api_base,
        api_key=args.api_key,
        model=args.model,
        timeout=args.timeout,  # None = 未指定，交给配置文件/默认值
        file_config=file_config,
    )
