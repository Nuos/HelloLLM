"""模块：query/agent_loop.py —— Agent-Loop 核心查询循环。

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
        └── manager.py                    HookManager：PreToolUse/PostToolUse 调度
    │
    └── permissions/                      八、权限系统（★ S04 新增，图1 "Permission System"）
        ├── __init__.py                   包入口（聚合导出）
        ├── policies.py                   工具→策略等级映射（deny-first）
        ├── modes.py                      权限模式（interactive / auto-accept / read-only）
        └── gate.py                       PermissionGate：execute 前检查（allow/ask/deny）
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
from ..permissions import PermissionGate  # S04：权限门（execute 前的系统级裁决）
from ..tools import TOOLS, execute  # 工具层：默认工具池 / 工具执行

# 一、网络自动重试上限（对齐用户"重试解决"偏好的有界版）
#     仅 network / timeout 类错误重试（http 重试无益，直接抛出）
MAX_NETWORK_RETRIES = 3  # 每轮模型调用最多自动重试次数（含首次共 4 次尝试）


def _retry_network(fn: Callable[[], Any], label: str = "模型调用") -> Any:
    """函数：网络错误自动重试包装。

一、功能作用（算法）
    对模型调用做有界重试：捕获 ModelError，仅 network/timeout 两类
    错误重试（http 错误重试无益直接抛），最多 MAX_NETWORK_RETRIES 次
    尝试，递增退避（1s/2s）；每次重试前输出警告日志，耗尽后原样抛出。

二、输入（input）
    fn：返回 ModelResponse 的调用函数（含 messages/tools/cfg 的闭包）。
    messages：OpenAI 格式消息列表。
    tools：工具 Schema 列表。
    cfg：模型调用配置。

三、输出（output）
    成功的 ModelResponse；重试耗尽后抛 ModelError。    """
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
        """方法：构造会话状态容器。

        一、功能作用
            初始化消息历史与模型配置：system 提示置首，其余字段置空，
            后续由 add_user/query_loop 填充；多轮 REPL 共用同一实例，
            消息跨轮次保留。

        二、输入（input）
            system_prompt：系统提示词文本（始终保留，不被裁剪）。
            cfg：模型调用配置；不传按默认构建。
            max_context_chars：滑动窗口上限（字符）；超限裁剪最老消息。

        三、输出（output）
            无返回值；构造完成的 Conversation 实例。
        """
        self.system_prompt = system_prompt
        self.cfg = cfg or ModelConfig()
        self.max_context_chars = max_context_chars
        # msgs：OpenAI 协议消息数组（system 打头，之后按 user/assistant/tool 交错追加）
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

    def add_user(self, text: str) -> None:
        """方法：构造会话状态容器。

一、功能作用
    初始化消息历史与模型配置：system 提示置首，其余字段置空，
    后续由 add_user/query_loop 填充。

二、输入（input）
    system_prompt：系统提示词文本。
    model_config：模型调用配置（可选，供循环使用）。

三、输出（output）
    无返回值；构造完成的 Conversation 实例。        """
        self.messages.append({"role": "user", "content": text})
        self._trim_to_budget()

    def _trim_to_budget(self) -> None:
        """函数：滑动窗口裁剪消息历史。

一、功能作用（算法）
    计算当前总字符数，超过预算时从最老消息（除 system 提示）开始
    逐条删除，直到低于预算；若单条消息本身超预算则整条丢弃。
    裁剪后输出提示，告知用户裁了多少。

二、输入（input）
    budget：上下文长度预算上限（--max-context）。

三、输出（output）
    无返回值；直接修改自身消息列表。        """
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
        """函数：清理残缺的工具调用序列。

一、功能作用（算法）
    消息历史里"助手发起工具调用但缺失对应 tool 结果"的序列会让
    模型协议校验失败（tool_call_id 无匹配）。本函数从尾部向前
    删除残缺的 tool_calls 消息，保证历史对模型协议合法。

二、输入（input）
    无（操作自身消息列表）。

三、输出（output）
    无返回值；直接修改自身消息列表。        """
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
    """函数：默认聚合模型调用（延迟导入适配层）。

一、功能作用
    把 call_model 的导入推迟到函数体内（避免模块级循环依赖），
    再转发调用。作为 query_loop 的默认 call_model 参数存在，
    测试可注入假模型替换它。

二、输入（input）
    messages：OpenAI 格式消息列表。
    tools：工具 Schema 列表。
    cfg：模型调用配置。

三、输出（output）
    call_model 的返回（ModelResponse）。    """
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
    permission_gate: Optional["PermissionGate"] = None,  # S04：权限门；不传则无权限检查（兼容旧测试）
) -> Iterator[dict]:
    """函数：Agent-Loop 主循环（生成器）。

一、功能作用（算法，论文图5 伪代码）
    while not stopped：
        a. 组装上下文（system 提示 + 工具 Schema + 滑动窗口内历史）；
        b. 模型调用（流式走 stream_model，否则走 call_model），
           网络/超时错误自动重试最多 3 次；
        c. 无工具调用 → 本轮完成、循环结束；
        d. 有工具调用 → 逐个执行：hook 前审（拦截/改写参数）→
           权限门（系统裁决）→ execute（真正执行）→ hook 后审（改写结果），
           结果作为 tool 角色消息回填历史，进入下一轮。
    全程以 yield 产出事件（text_delta/tool_use/tool_result/model_error），
    由 render_events 消费渲染。

二、输入（input）
    conversation：会话状态（消息历史在循环内被读写）。
    tools：工具 Schema 列表；None 用内置文件工具。
    max_turns：循环轮数上限（论文 §4.5 停止条件 2）。
    call_model：聚合模型调用（测试注入点）。
    execute_tool：工具执行函数（测试注入点）。
    stream_model：流式模型通道；为真时走真·逐字流式。
    hook_manager：工具管理中枢；不传则与 S01 行为一致。
    permission_gate：权限门；不传则无权限检查。

三、输出（output）
    事件字典流：text_delta（逐字文本）、tool_use（工具调用）、
    tool_result（工具结果）、model_error（模型错误）。    """
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
                # ── 权限门（S04）：hook 放行后、工具执行前的系统级裁决 ──
                #    hook 是用户自定义管理（可改写参数），权限门是内置策略
                #    （按工具等级 + 权限模式裁决）；被拒则不执行，拒绝原因回填模型。
                if permission_gate is not None and not permission_gate.decide(tc.name, pre["arguments"]):
                    result_text = (
                        f"错误：权限拒绝（工具 {tc.name} 未获批准，当前模式 {permission_gate.mode}）"
                    )
                    log_warn(f"权限拒绝：{tc.name} 未获批准（模式 {permission_gate.mode}）")
                else:
                    # hook 与权限都放行了：用最终参数真正执行工具
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
