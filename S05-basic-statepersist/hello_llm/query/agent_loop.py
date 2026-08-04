"""模块：query/agent_loop.py —— Agent-Loop 核心查询循环。

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
    1.  Agent-Loop —— 智能体循环（本模块即其实现）
    2.  ReAct —— Reasoning + Acting：推理与行动交替的智能体模式（论文 §4.1）
    3.  API —— Application Programming Interface，应用程序编程接口
    4.  JSON —— JavaScript Object Notation，轻量数据交换格式
"""

from __future__ import annotations  # 延迟求值注解

import json  # 工具参数在 OpenAI 协议里是 JSON 字符串，序列化用
import time  # 自动重试的递增退避等待（1s/2s/…）
from typing import Any, Callable, Iterator, Optional  # 类型标注

from ..utils import (  # 日志提示层：业务事件通知（裁剪/轮次/工具/限制）
    context_trimmed,  # 上下文裁剪提示（超预算）
    loop_turn,  # Agent-Loop 轮次提示
    max_turns_reached,  # 最大轮数提示（循环限制）
    tool_result_summary,  # 工具执行结果提示
    tool_triggered,  # 工具触发提示
    warn as log_warn,  # 警告级提示（自动重试通知）
)
from ..services.api import consume_stream, ModelConfig, ModelError, ModelResponse  # 模型层：流式聚合/配置/响应
from ..hooks import HookManager  # S03：工具管理中枢（工具执行前后跑 hook）
from ..tools import TOOLS, execute  # 工具层：默认工具池 / 工具执行

# 一、网络自动重试上限（对齐用户"重试解决"偏好的有界版）
#     仅 network / timeout 类错误重试（http 重试无益，直接抛出）
MAX_NETWORK_RETRIES = 3  # 每轮模型调用最多自动重试次数（含首次共 4 次尝试）


def _retry_network(fn: Callable[[], Any], label: str = "模型调用") -> Any:
    """函数：网络类错误自动重试包装（聚合通道用）。

    一、功能作用
        call_model 聚合调用失败（连接重置/超时）时自动重试，
        避免用户手动反复重发；http 类错误不重试（重试无益）。

    二、参数
        fn    （Callable）无参调用（闭包捕获 msgs/tools/cfg）
        label （str）日志提示前缀（如"模型调用"）

    三、返回
        fn() 的结果；重试耗尽后原样抛出 ModelError。
    """
    for attempt in range(1, MAX_NETWORK_RETRIES + 1):
        try:
            return fn()
        except ModelError as e:  # e：模型调用异常（含 kind 分类）
            if e.kind not in ("network", "timeout") or attempt == MAX_NETWORK_RETRIES:
                raise  # 非网络错误 / 已达上限 → 原样抛出（render 层转为中文提示）
            log_warn(f"{label}网络错误（{e}），自动重试 {attempt}/{MAX_NETWORK_RETRIES}…")
            time.sleep(attempt)  # 递增退避：第 1 次等 1s，第 2 次等 2s

# 一、默认值
DEFAULT_MAX_CONTEXT_CHARS = 30_000  # 滑动窗口上限（字符）：超出裁剪最老消息


class Conversation:
    """数据结构：一次会话的状态 —— 对应论文 queryLoop 中的 State 对象。

    一、功能作用
        论文 §4.1："单个 State 对象保存跨迭代的全部可变状态，包括消息……"
        v1 只保留消息历史：messages 跨轮次保留，使 REPL 能多轮、多回合对话。

    二、多轮可持续性（滑动窗口）
        对话无限增长会撑爆模型的上下文窗口（API 报 prompt_too_long，
        论文 §4.5 停止条件之一）。v1 用简单滑动窗口解决：超过
        max_context_chars 字符时，从最老的对话消息开始整条丢弃
        （永远保留 system 提示词）。论文的完整方案（五层压缩流水线，
        §4.3 / §7.3）留给后续版本。
    """

    def __init__(
        self,
        system_prompt: str,  # 系统提示词：定义 Agent 身份与行为准则，始终保留
        cfg: Optional[ModelConfig] = None,  # 模型配置；缺省时按默认构建
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,  # 滑动窗口上限（字符）
    ):
        self.system_prompt = system_prompt
        self.cfg = cfg or ModelConfig()
        self.max_context_chars = max_context_chars
        # msgs：OpenAI 协议消息数组（system 打头，之后按 user/assistant/tool 交错追加）
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

    def add_user(self, text: str) -> None:
        """方法：追加一条用户消息，并触发滑动窗口裁剪。

        一、功能作用
            REPL/无头模式输入入口：把用户提问写入历史，
            并保证历史不超过上下文预算（多轮对话的上下文守卫）。
        """
        self.messages.append({"role": "user", "content": text})
        self._trim_to_budget()

    def _trim_to_budget(self) -> None:
        """方法：滑动窗口裁剪 —— 总字符数超限时删最老消息，并保证工具消息配对完整。

        一、功能作用
            1. 约束：永远保留 messages[0]（system 提示词）和至少一条最新消息，
               保证模型始终能看到"系统身份 + 当前问题"；
            2. 裁剪后执行 _prune_incomplete_tool_sequences()，防止产生
               违反 OpenAI 协议的孤立工具消息（服务端会报 HTTP 400）。
        """
        total = sum(len(m.get("content") or "") for m in self.messages)  # total：历史总字符数
        removed = 0  # removed：本次裁剪的消息条数（日志统计用）
        while total > self.max_context_chars and len(self.messages) > 2:
            removed_msg = self.messages.pop(1)  # removed_msg：被删的最老非 system 消息
            total -= len(removed_msg.get("content") or "")
            removed += 1
        if removed:
            # 日志提示：通知用户"历史被截断"（影响模型对早期内容的记忆）
            context_trimmed(removed, total, self.max_context_chars)
        self._prune_incomplete_tool_sequences()  # 协议一致性守卫（见下）

    def _prune_incomplete_tool_sequences(self) -> None:
        """方法：删除不完整的工具调用序列（协议一致性守卫）。

        一、功能作用
            OpenAI 协议要求 role="tool" 的消息必须紧跟（响应）一个带
            tool_calls 的 assistant 消息。滑动窗口逐条删除可能把一对拆开
            （删掉 assistant 留下 tool），服务端会报 HTTP 400：
            "Messages with role 'tool' must be a response to a preceding
            message with 'tool_calls'"。本方法保证历史里不存在孤立消息：
            1. assistant(tool_calls) 后面没有 tool 响应 → 整组删除；
            2. 孤立的 tool 消息（前面无 assistant(tool_calls)）→ 删除。
        """
        msgs = self.messages  # msgs：消息历史别名
        out: list[dict[str, Any]] = [msgs[0]]  # out：清理后的消息（保留 system 提示词）
        i = 1  # i：遍历游标（从索引 1 开始，跳过 system）
        while i < len(msgs):
            m = msgs[i]  # m：当前消息
            if m.get("role") == "assistant" and m.get("tool_calls"):
                # 收集紧随其后的连续 tool 消息（同一轮的工具执行结果）
                j = i + 1  # j：tool 消息游标
                tools: list[dict[str, Any]] = []  # tools：本 assistant 的工具结果列表
                while j < len(msgs) and msgs[j].get("role") == "tool":
                    tools.append(msgs[j])
                    j += 1
                if tools:
                    out.append(m)  # 配对完整：assistant(tool_calls) + tool 结果整组保留
                    out.extend(tools)
                # 无 tool 响应：assistant(tool_calls) 整组丢弃（无结果的调用无意义）
                i = j
                continue
            if m.get("role") == "tool":
                i += 1  # 孤儿 tool 消息（前面无配对的 assistant）：丢弃
                continue
            out.append(m)  # 普通消息（user / assistant 文本）：保留
            i += 1
        self.messages = out


def call_model_fallback(
    messages: list[dict[str, Any]],  # msgs：对话历史
    tools: Optional[list[dict]] = None,  # 工具 Schema
    cfg: Optional[ModelConfig] = None,  # 请求配置
) -> ModelResponse:
    """函数：默认聚合版模型调用（query_loop 的 call_model 注入点默认值）。

    一、功能作用
        包一层 services/api/client.call_model，使 query_loop 的默认参数
        无需在定义处直接依赖具体实现，测试可注入假模型替换。

    二、注意
        必须定义在 query_loop 之前 —— Python 在 def 语句执行时求值默认参数，
        若在其后定义会触发 NameError。
    """
    from ..services.api import call_model  # 延迟导入：避免模块级循环依赖

    return call_model(messages, tools, cfg)


def query_loop(
    conversation: Conversation,  # conv：会话状态（消息历史在循环内被读写）
    tools: Optional[list[dict]] = None,  # 工具 Schema 列表（OpenAI 格式）；None 用内置文件工具
    max_turns: int = 10,  # 循环轮数上限（论文 §4.5 停止条件 2）
    call_model: Callable = call_model_fallback,  # 聚合版模型调用（测试注入点）
    execute_tool: Callable = execute,  # 工具执行函数（测试注入点）
    stream_model: Optional[Callable] = None,  # 流式模型通道；为真时走真·逐字流式
    hook_manager: Optional["HookManager"] = None,  # S03：工具管理中枢；不传则与 S01 行为一致
) -> Iterator[dict]:
    """生成器：Agent-Loop —— 每次迭代产出事件，无 tool_use 时本轮完成、循环结束。

    一、功能作用（实现论文图5 伪代码）
        while not stopped:
            context = assemble(system_prompt, tool_schemas, history)  # a. 组装
            action = model(context, tools)                            # b. 模型调用（流式）
            if action.is_text_only(): stopped = ...; continue         #    停止条件（§4.5）
            result = execute(action)                                  # c. 执行工具
            history.append(action, result)

    二、参数
        conversation  （Conversation）会话状态；messages 在循环内被读写
        tools         （list|None）工具 Schema；None 用内置文件工具池
        max_turns     （int）循环轮数上限
        call_model    （Callable）聚合版模型调用（测试注入点）
        execute_tool  （Callable）工具执行函数（测试注入点）
        stream_model  （Callable|None）流式模型通道；为真时逐字产出增量

    三、返回
        Iterator[dict]，事件类型（entrypoints/render.py 按 type 分派）：
            text_delta       流式文本（逐字）
            reasoning_delta  推理模型思维链
            tool_use         模型要调用工具 {"id","name","arguments"}
            tool_result      工具执行结果 {"name","content"}
            turn_end         一轮结束（模型纯文本回复）{"text"}
            done             整个 query 结束 {"reason"}

    4. 停止条件（论文 §4.5）
        - 无工具使用（模型只产生文本）—— 主要停止条件
        - 达到 max_turns 上限
        - 显式中止（Ctrl-C / 异常，由 render.py 层处理）
"""
    if max_turns < 1:
        raise ValueError("max_turns 必须 >= 1")

    msgs = conversation.messages  # msgs：消息历史别名（循环直接读写会话状态）
    tool_schemas = tools if tools is not None else TOOLS  # tool_schemas：本轮工具池

    for turn in range(1, max_turns + 1):  # turn：当前轮次（1 起）
        # 日志提示：本轮开始，调用模型（模块触发通知）
        loop_turn(turn, max_turns, conversation.cfg.model)
        # ── b. model：模型调用（assemble 即 msgs 本身，v1 无额外上下文组装）──
        if stream_model is not None:
            # 流式通道：consume_stream 边转发增量边聚合，最终产出 ModelResponse
            response: Optional[ModelResponse] = None  # response：本轮聚合结果
            for attempt in range(1, MAX_NETWORK_RETRIES + 1):  # attempt：重试计数
                try:
                    for event in consume_stream(
                        stream_model(msgs, tool_schemas, conversation.cfg)
                    ):
                        if event["type"] == "model_response":
                            response = event["response"]
                        else:
                            yield event  # text_delta / reasoning_delta 原样转发给 UI 层
                    break  # 流正常结束（无异常）→ 退出重试循环
                except ModelError as e:  # e：模型调用异常
                    if e.kind not in ("network", "timeout") or attempt == MAX_NETWORK_RETRIES:
                        raise  # 非网络错误 / 已达上限 → 原样抛出
                    log_warn(f"流式模型调用网络错误（{e}），自动重试 {attempt}/{MAX_NETWORK_RETRIES}…")
                    time.sleep(attempt)  # 递增退避：第 1 次等 1s，第 2 次等 2s
        else:
            # 聚合通道：一次性拿到完整响应（测试注入的假模型走这里）
            response = _retry_network(
                lambda: call_model(msgs, tool_schemas, conversation.cfg)  # 闭包：重试时重新调用
            )
            if response.text:
                yield {"type": "text_delta", "text": response.text}

        if not response.has_tool_calls:
            # ── 停止条件 1（§4.5）：模型只产生文本，本轮完成 ──
            msgs.append({"role": "assistant", "content": response.text})
            yield {"type": "turn_end", "text": response.text}
            yield {"type": "done", "reason": "no_tool_use"}
            return

        # ── c. execute：模型请求了工具调用 ──
        # 先把 tool_calls 转成 OpenAI 协议格式（arguments 必须是 JSON 字符串），
        # 作为 assistant 消息记入历史（论文：history.append(action, result)）
        tool_calls = [  # tool_calls：OpenAI 协议格式的工具调用列表
            {
                "id": tc.id,  # tc：单个 ToolCall（模型层数据结构）
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),  # 参数序列化为 JSON 串
                },
            }
            for tc in response.tool_calls
        ]
        msgs.append(
            {"role": "assistant", "content": response.text or None, "tool_calls": tool_calls}
        )

        # 逐个执行工具调用，结果作为 tool 角色消息回填历史（论文图5 的
        # history.append(action, result)），供下一轮模型调用看到
        for tc in response.tool_calls:  # tc：单个工具调用
            yield {
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments,
            }
            # 工具触发日志：这一轮模型点名要用这个工具，先记一笔再往下走
            tool_triggered(tc.name, tc.arguments)
            # ── hook 机制（S03）：工具执行前的管理 ──
            #    依次询问配置好的 hook 命令：它说拒绝，工具就不执行，
            #    拒绝原因作为工具结果回填给模型，模型会据此调整下一步；
            #    它想改写参数，改写结果就直接用于本次真正的工具调用。
            pre = hook_manager.run_pre_tool(tc.name, tc.arguments) if hook_manager else {
                "allow": True, "arguments": tc.arguments, "reason": None
            }
            if not pre["allow"]:
                # hook 拒绝了：工具不执行，把拒绝原因拼进工具结果回填模型
                result_text = f"错误：hook 拒绝（工具 {tc.name}：{pre['reason']}）"
                log_warn(f"hook 拒绝：{tc.name}（{pre['reason']}）")
            else:
                # hook 放行了：用（可能被改写后的）参数真正执行工具
                result_text = execute_tool(tc.name, pre["arguments"])
                # ── hook 机制（S03）：工具执行后的管理 ──
                #    把工具的真实结果交给配置好的 hook 命令过目，
                #    它想改写就把改写后的文本作为最终结果回填给模型。
                if hook_manager is not None:
                    result_text = hook_manager.run_post_tool(tc.name, pre["arguments"], result_text)
            # 工具结果日志：记录这次工具调用最终交回给模型的结果
            tool_result_summary(tc.name, result_text)
            yield {"type": "tool_result", "name": tc.name, "content": result_text}
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})

    # ── 停止条件 2（§4.5）：达到 max_turns，仍未出现纯文本回复 ──
    # 日志提示：通知用户循环被轮数限制终止
    max_turns_reached(max_turns)
    yield {"type": "done", "reason": "max_turns"}
