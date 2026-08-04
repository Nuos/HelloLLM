"""模块：services/api/claude.py —— OpenAI 兼容 SSE 流式客户端。

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
    │       ├── claude.py                 stream_chat：SSE 流式客户端（对照 claude.ts）★★★ 本模块 ★★★
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
    ├── hooks/                            七、hook 机制（★ S03 新增，对照 src/utils/hooks.ts）
        ├── __init__.py                   包入口（聚合导出）
        ├── config.py                     加载 hook 规则（项目内 + 用户级合并）
        ├── runner.py                     subprocess 执行 hook（stdin/stdout JSON）
        ├── manager.py                    HookManager：PreToolUse/PostToolUse 调度
    │
    └── state/                            八、状态与持久化（★ S05 新增，图1 "State & Persistence"）
        ├── __init__.py                   包入口（聚合导出）
        ├── store.py                      会话 JSONL 存储（对照 src/utils/sessionStorage.ts）
        └── session.py                    会话名生成与元数据
缩略词说明（本模块涉及的术语）：
    1.  API —— Application Programming Interface，应用程序编程接口
    2.  SSE —— Server-Sent Events，服务器推送事件（HTTP 流式协议，逐块推送）
    3.  HTTP —— HyperText Transfer Protocol，超文本传输协议
    4.  JSON —— JavaScript Object Notation，轻量数据交换格式（SSE 载荷格式）
    5.  Bearer —— HTTP 鉴权方案（Authorization: Bearer <token>）
    6.  DNS —— Domain Name System，域名系统
    7.  URL —— Uniform Resource Locator，统一资源定位符
    8.  UTF-8 —— 8-bit Unicode Transformation Format，可变长字符编码
"""

from __future__ import annotations  # 延迟求值注解

import json  # SSE 载荷是 JSON，逐块解析
import socket  # 识别 socket.timeout（超时异常）
import urllib.error  # 捕获 HTTPError / URLError
import urllib.request  # 发送 HTTP POST 并逐行读取流式响应
from typing import Any, Iterator, Optional  # 类型标注

from .config import ModelConfig  # 请求配置（端点/密钥/模型/超时）
from .types import ModelError  # 模型调用异常


def stream_chat(
    messages: list[dict[str, Any]],  # msgs：OpenAI 协议消息数组（system/user/assistant/tool）
    tools: Optional[list[dict]] = None,  # 工具 Schema 列表（OpenAI function calling 格式）
    cfg: Optional[ModelConfig] = None,  # 配置；缺省时由 ModelConfig 默认构建
) -> Iterator[dict]:
    """函数：流式调用模型，逐事件产出（SSE 协议解析层）。

    一、功能作用
        纯标准库实现 OpenAI 兼容 chat/completions 流式接口：
        POST {api_base}/chat/completions，stream=true，服务端按 SSE
        逐行推送 data: {json}，以 data: [DONE] 结束。
        本函数逐行解析，把每个增量块映射为内部事件，供
        client.consume_stream 消费（UI 层由此获得逐字输出）。

    二、参数
        messages  （list）对话历史（含 system 提示词）
        tools     （list|None）工具 Schema；传入后模型才可能调用工具
        cfg       （ModelConfig|None）请求配置

    三、返回
        Iterator[dict]，事件类型：
            text_delta       答案正文增量 {"type","text"}
            reasoning_delta  推理模型思维链增量 {"type","text"}
            tool_call_delta  工具调用增量 {"type","index","id","name","arguments"}

    四、异常处理（基本异常面）
        1. HTTPError（4xx/5xx）→ ModelError，带服务端错误体（401 密钥错/400 参数错）
        2. URLError / OSError → ModelError，分类提示（DNS/连接拒绝/超时）
           注意：urllib 超时抛 socket.timeout（TimeoutError，OSError 子类），
           URLError 捕获不到 —— 必须单独处理，否则超时会裸抛。

    五、推理模型特例
        deepseek-v4-flash 等推理模型先大量推送 reasoning_content（思维链，
        约 40+ 块），最后才推送 content（答案正文，约 3 块）。
        只收 content 会表现为"没回答"，因此两者分别映射为
        reasoning_delta / text_delta 事件。
    """
    cfg = cfg or ModelConfig()
    payload: dict[str, Any] = {  # payload：HTTP 请求体（OpenAI chat/completions 协议）
        "model": cfg.model,  # 模型名
        "messages": messages,  # 对话历史
        "stream": True,  # 关键开关：请求服务端以 SSE 流式返回
    }
    if tools:
        payload["tools"] = tools  # 工具 Schema：模型据此决定是否调用工具
    if cfg.max_tokens:
        payload["max_tokens"] = cfg.max_tokens  # 输出 token 上限

    # 构造 HTTP 请求：JSON body + Bearer 鉴权
    req = urllib.request.Request(  # req：HTTP 请求对象
        f"{cfg.api_base}/chat/completions",  # OpenAI 兼容路径
        data=json.dumps(payload).encode("utf-8"),  # 请求体：JSON 序列化 + UTF-8
        headers={
            "Content-Type": "application/json",  # 内容类型：JSON
            "Authorization": f"Bearer {cfg.api_key}",  # 鉴权：Bearer 令牌
        },
        method="POST",  # HTTP 方法
    )
    try:
        resp = urllib.request.urlopen(req, timeout=cfg.timeout)  # resp：HTTP 响应
    except urllib.error.HTTPError as e:  # e：HTTP 错误对象
        # 4xx/5xx：把服务端错误体带出来，便于定位（401=密钥无效，400=参数错）
        body = e.read().decode("utf-8", "replace")[:500]  # body：服务端错误体（截断）
        if e.code == 429:
            # 日志提示：额度/频率限制（用户必须知晓的限制事件）
            # 延迟导入：避免 utils ↔ services.api 循环导入（与 agent_loop 同模式）
            from ...utils import rate_limited

            rate_limited(e.code, body[:200])
        raise ModelError(f"HTTP {e.code}: {body}", kind="http") from e
    except (urllib.error.URLError, OSError) as e:  # e：网络层异常
        # URLError 覆盖 DNS/连接拒绝；OSError 覆盖 socket 层异常（含超时）
        if isinstance(e, (socket.timeout, TimeoutError)):
            # 超时优先提示：推理模型思考久，用户第一反应是"是不是卡了"
            raise ModelError(
                f"请求超时（>{cfg.timeout} 秒）。可稍后重试，或用 --timeout 调大。",
                kind="timeout",
            ) from e
        reason = getattr(e, "reason", e)  # reason：底层原因
        raise ModelError(f"网络错误: {reason}", kind="network") from e

    # ── SSE 逐行解析：resp 可迭代，每项一行 ──
    for raw in resp:  # raw：响应的一行原始字节
        line = raw.decode("utf-8", "replace").strip()  # line：去除空白后的行文本
        if not line or line.startswith(":"):  # SSE 注释行（keep-alive 心跳）
            continue
        if not line.startswith("data:"):  # 非 data 行（如空行分隔符）
            continue
        data = line[5:].strip()  # data：去掉 "data:" 前缀后的载荷
        if data == "[DONE]":  # 结束哨兵：流终止
            break
        try:
            event = json.loads(data)  # event：解析后的 SSE 事件（dict）
        except json.JSONDecodeError:
            continue  # 忽略坏块，健壮性优先
        choices = event.get("choices") or []  # choices：OpenAI 流式格式的选择列表
        if not choices:
            continue
        delta = choices[0].get("delta") or {}  # delta：增量块（正文/思维链/工具调用）
        if delta.get("content"):  # 答案正文增量
            yield {"type": "text_delta", "text": delta["content"]}
        if delta.get("reasoning_content"):  # 推理模型思维链增量
            yield {"type": "reasoning_delta", "text": delta["reasoning_content"]}
        for tc in delta.get("tool_calls") or []:  # tc：工具调用增量块
            fn = tc.get("function") or {}  # fn：增量块里的 function 字段
            yield {
                "type": "tool_call_delta",
                "index": tc.get("index", 0),  # index：同一工具调用的块归组标识
                "id": tc.get("id", ""),  # id：工具调用 ID（通常只在第一块出现）
                "name": fn.get("name", ""),  # name：工具名（通常只在第一块出现）
                "arguments": fn.get("arguments", ""),  # arguments：参数 JSON 串（分块累积）
            }
