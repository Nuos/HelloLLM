"""模块：query/agent_loop.py —— Agent-Loop 核心查询循环。

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
    │   └── agent_loop.py                 query_loop() 生成器 + Conversation（对照 queryLoop）★★★ 本模块 ★★★
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
    └── utils/                            六、工具函数层（对照 src/utils/：config.ts 等）
        ├── __init__.py
        ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）
        └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）缩略词说明（本模块涉及的术语）：
    1.  Agent-Loop —— 智能体循环（本模块即其实现）
    2.  ReAct —— Reasoning + Acting：推理与行动交替的智能体模式（论文 §4.1）
    3.  API —— Application Programming Interface，应用程序编程接口
    4.  JSON —— JavaScript Object Notation，轻量数据交换格式
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Iterator, Optional

from ..utils import (
    context_trimmed,
    loop_turn,
    max_turns_reached,
    tool_result_summary,
    tool_triggered,
    warn as log_warn,
)
from ..services.api import consume_stream, ModelConfig, ModelError, ModelResponse
from ..tools import TOOLS, execute



MAX_NETWORK_RETRIES = 3


def _retry_network(fn: Callable[[], Any], label: str = "模型调用") -> Any:
    for attempt in range(1, MAX_NETWORK_RETRIES + 1):
        try:
            return fn()
        except ModelError as e:
            if e.kind not in ("network", "timeout") or attempt == MAX_NETWORK_RETRIES:
                raise
            log_warn(f"{label}网络错误（{e}），自动重试 {attempt}/{MAX_NETWORK_RETRIES}…")
            time.sleep(attempt)


DEFAULT_MAX_CONTEXT_CHARS = 30_000


class Conversation:
    def __init__(
        self,
        system_prompt: str,
        cfg: Optional[ModelConfig] = None,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    ):
        self.system_prompt = system_prompt
        self.cfg = cfg or ModelConfig()
        self.max_context_chars = max_context_chars

        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self._trim_to_budget()

    def _trim_to_budget(self) -> None:
        total = sum(len(m.get("content") or "") for m in self.messages)
        removed = 0
        while total > self.max_context_chars and len(self.messages) > 2:
            removed_msg = self.messages.pop(1)
            total -= len(removed_msg.get("content") or "")
            removed += 1
        if removed:

            context_trimmed(removed, total, self.max_context_chars)
        self._prune_incomplete_tool_sequences()

    def _prune_incomplete_tool_sequences(self) -> None:
        msgs = self.messages
        out: list[dict[str, Any]] = [msgs[0]]
        i = 1
        while i < len(msgs):
            m = msgs[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):

                j = i + 1
                tools: list[dict[str, Any]] = []
                while j < len(msgs) and msgs[j].get("role") == "tool":
                    tools.append(msgs[j])
                    j += 1
                if tools:
                    out.append(m)
                    out.extend(tools)

                i = j
                continue
            if m.get("role") == "tool":
                i += 1
                continue
            out.append(m)
            i += 1
        self.messages = out


def call_model_fallback(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict]] = None,
    cfg: Optional[ModelConfig] = None,
) -> ModelResponse:
    # QQQ（已答）：它"看似无用"，实际是 query_loop 的"模型调用策略插槽"。
    # query_loop 支持两种调用模型的方式：stream_model（流式，逐字输出）或
    # call_model（整段返回）。call_model_fallback 作为 call_model 参数的默认值，
    # 把"调用模型的策略"封装成一个可替换的函数：
    # - 默认行为：直接转发给真正的 call_model（不做任何加工）；
    # - 将来想加缓存/日志/换实现，只需换掉这个参数，query_loop 本体不用改。
    # 这是"依赖注入/策略模式"的最小形态——不是空壳，是给未来留的插槽。
    from ..services.api import call_model

    return call_model(messages, tools, cfg)


def query_loop(
    conversation: Conversation,
    tools: Optional[list[dict]] = None,
    max_turns: int = 10,
    call_model: Callable = call_model_fallback,
    execute_tool: Callable = execute,
    stream_model: Optional[Callable] = None,
) -> Iterator[dict]:
    # QQQ157/QQQ158/QQQ159（已答）：你的理解完全正确，三点都成立：
    # ① query_loop 是"生成器函数"：调用它不立即执行，而是返回一个迭代器；
    # ② 迭代器逐个产生"事件字典"：text_delta（文本增量）、tool_use（模型要调工具）、
    #    tool_result（工具执行结果）、turn_end（本轮结束）、done（循环结束）等；
    # ③ 它的职责就是"管理 Agent 与模型/工具的交互循环"：每轮 = 调模型 → 看是否要
    #    工具 → 要则执行并回填结果 → 下一轮；直到模型不再要工具（no_tool_use）
    #    或达到 max_turns 上限。渲染层（render.py）for event in query_loop(...)
    #    逐个消费这些事件，实现流式输出与状态展示。
    if max_turns < 1:
        raise ValueError("max_turns 必须 >= 1")

    msgs = conversation.messages
    tool_schemas = tools if tools is not None else TOOLS

    for turn in range(1, max_turns + 1):
        # QQQ（已答）：是。这一行就是"轮次上限"：for turn in range(1, max_turns + 1)。
        # 一轮（turn）= 一次"模型调用 + 可能的工具执行"；用户每提问一次，
        # Agent 最多与模型/工具循环交互 max_turns 轮（默认 10，可 --max-turns 改）。
        # 循环内如果模型不再要工具（no_tool_use）会提前 return；全跑完仍未结束
        # 则最后 yield done(reason=max_turns)，防止无限循环。
        loop_turn(turn, max_turns, conversation.cfg.model)

        if stream_model is not None:

            response: Optional[ModelResponse] = None
            for attempt in range(1, MAX_NETWORK_RETRIES + 1):
                # QQQ（已答）：是。这是"网络重试上限"：for attempt in range(1, MAX_NETWORK_RETRIES + 1)。
                # 流式调用抛 ModelError 时，仅当错误属于 network/timeout 类才重试
                # （最多 MAX_NETWORK_RETRIES 次，退避等待 sleep(attempt) 秒）；
                # 其他错误（如 401 鉴权失败）或重试耗尽则直接抛出。这是 S01 定下的
                # "网络自动重试"规范：网络抖动重试，业务错误不掩盖。
                try:
                    for event in consume_stream(
                        stream_model(msgs, tool_schemas, conversation.cfg)
                    ):
                        # QQQ（已答）：是。consume_stream(...) 是生成器函数（client.py 里带 yield），
                        # 它接收 stream_chat 的原始 SSE 流，逐块解析后 yield 事件字典：
                        #   - model_response 事件：携带完整 ModelResponse（文本+工具调用）→ 记入 response；
                        #   - 其他事件（text_delta 文本增量 / reasoning_delta 思考过程等）→ 向上转发
                        #     （yield event），渲染层收到后逐字打印，实现"流式输出"。
                        if event["type"] == "model_response":
                            response = event["response"]
                        else:
                            yield event
                    break
                except ModelError as e:
                    if e.kind not in ("network", "timeout") or attempt == MAX_NETWORK_RETRIES:
                        raise
                    log_warn(f"流式模型调用网络错误（{e}），自动重试 {attempt}/{MAX_NETWORK_RETRIES}…")
                    time.sleep(attempt)
        else:

            response = _retry_network(
                lambda: call_model(msgs, tool_schemas, conversation.cfg)
            )
            if response.text:
                yield {"type": "text_delta", "text": response.text}

        if not response.has_tool_calls:

            msgs.append({"role": "assistant", "content": response.text})
            yield {"type": "turn_end", "text": response.text}
            yield {"type": "done", "reason": "no_tool_use"}
            return




        # QQQ（已答）：这是"协议转换"，不是拼装 schema。TOOLS 常量才是工具 schema
        # （发给模型看的"工具有哪些、参数长什么样"）；这里的 tool_calls 是"调用记录"：
        # 把模型返回的内部 ToolCall 对象（Python 字典 arguments）转成 OpenAI 协议要求的
        # 格式——arguments 必须是 JSON 字符串，所以用 json.dumps 序列化。
        # 业务含义：作为 role=assistant 消息的 tool_calls 字段记入历史（下一行 msgs.append），
        # 下一轮把历史发给 API 时，API 才能识别"模型上一轮调用了哪些工具"。
        tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in response.tool_calls
        ]
        msgs.append(
            {"role": "assistant", "content": response.text or None, "tool_calls": tool_calls}
        )



        for tc in response.tool_calls:
            yield {
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments,
            }

            # QQQ（已答）：逐个处理模型要求执行的工具，五步走：
            # ① yield tool_use 事件——通知渲染层"模型要调用工具 X"，界面可显示；
            # ② tool_triggered()——打印工具触发日志（⚙ 紫色提示）；
            # ③ execute_tool()——真正执行工具：registry 按名字分发到具体实现
            #    （read_file/write_file/bash…），返回文本结果；
            # ④ tool_result_summary()——打印结果摘要日志；
            # ⑤ yield tool_result 事件 + 把结果作为 role=tool 消息记入历史
            #    （msgs.append）——下一轮模型就能"看到"工具输出，继续推理。
            tool_triggered(tc.name, tc.arguments)
            result_text = execute_tool(tc.name, tc.arguments)

            tool_result_summary(tc.name, result_text)
            yield {"type": "tool_result", "name": tc.name, "content": result_text}
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})



    max_turns_reached(max_turns)
    yield {"type": "done", "reason": "max_turns"}
"""
        生成器解决的核心问题是：

            一个函数需要分多次产生结果，
            同时保留上一次执行位置和局部状态，
            而不是一次性计算完全部结果再返回。

        普通函数的执行模型是：

            调用函数
            ↓
            从头执行到 return
            ↓
            返回一个结果
            ↓
            函数栈帧销毁

        生成器函数的执行模型是：

            调用生成器函数
            ↓
            创建生成器对象，但通常还未执行函数体
            ↓
            next() / for 请求下一个结果
            ↓
            执行到 yield
            ↓
            返回一个结果，并暂停
            ↓
            保存局部变量和执行位置
            ↓
            再次 next()
            ↓
            从 yield 后面继续

        query_loop() 就是典型生成器函数：
        它没有一次性返回整个 Agent 执行结果，
        而是依次产生 text_delta、tool_use、tool_result、turn_end、done 等事件。

   
        表达“逐步产生结果”的业务

            很多业务本身就不是“一次性得到最终答案”，而是持续产生中间结果：

            文件逐行读取；
            网络数据流；
            SSE 流式响应；
            大模型 Token 流；
            数据库分页；
            目录递归遍历；
            Agent 执行事件；
            日志流；
            无限序列；
            解析器逐个产生 Token。

"""