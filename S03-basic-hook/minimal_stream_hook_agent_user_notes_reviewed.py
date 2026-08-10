#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minimal_stream_hook_agent.py
============================

一个“单文件、零第三方运行时依赖、真正 stream 模式”的最小 Coding Agent。

设计依据
--------
1. HelloLLM / S03-basic-hook
   - hello_llm/query/agent_loop.py
   - hello_llm/hooks/config.py
   - hello_llm/hooks/runner.py
   - hello_llm/hooks/manager.py
   - hello_llm/services/api/claude.py
   - hello_llm/tools/registry.py

2. Claude Code 源码（Nuos/claude-code-claude-code-source-code-full）
   - src/query.ts
   - src/utils/hooks.ts
   - src/services/tools/StreamingToolExecutor.ts
   - src/services/tools/toolOrchestration.ts

3. 论文
   - Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems
     arXiv:2604.14228v2
   - ReAct: Synergizing Reasoning and Acting in Language Models
     arXiv:2210.03629

核心循环
--------
用户 -> LLM(SSE stream) -> tool_call? -> PreToolUse Hook
                                      -> Tool
                                      -> PostToolUse Hook
                                      -> tool_result 回填 LLM
                                      -> 下一轮 LLM
                             无 tool_call -> 输出完成

为什么 Hook 放在 Tool 前后？
----------------------------
Hook 不是“工具本身”，而是 Agent Runtime 的控制/扩展切面：
- PreToolUse：工具真正产生副作用之前，做审核、拒绝、参数改写；
- PostToolUse：工具已经执行之后，但结果还没回填模型之前，做脱敏、审计、
  质量检查、结果增强。

这个文件为了保持“一个 Python 文件”：
- 主进程运行 Agent；
- 同一文件还可被主进程作为 subprocess 再次启动，充当 hook worker；
- 主进程与 hook worker 通过 stdin/stdout JSON 通信。
这与 HelloLLM S03 的 hook runner 思路一致，但这里不用额外 hooks.json / hook 脚本。

运行要求
--------
Python 3.10+，仅标准库。

配置文件（沿用 HelloLLM S03 的形式）：
~/.hellollm/config.json

示例：
{
  "api_key": "sk-...",
  "api_base": "https://api.deepseek.com",
  "model": "deepseek-v4-flash",
  "timeout": 120
}

也可以临时通过命令行覆盖：
python3 minimal_stream_hook_agent.py \
  --api-key "sk-..." \
  --api-base "https://api.deepseek.com" \
  --model "deepseek-v4-flash" \
  -p "读取 README.md 并总结"

交互模式：
python3 minimal_stream_hook_agent.py

退出：
/exit

重要边界
--------
1. Hook 不是 OS Sandbox。
2. 本示例的写入范围由 Tool Executor 自身再次限制在 --root 工作区内；
   即使 Hook 故障并按 S03 的 fail-open 继续，工具执行层仍有路径边界。
3. 生产环境不应只依赖 Prompt 或 Hook 来做安全隔离。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional


# ============================================================================
# 0. 基本配置
# ============================================================================

DEFAULT_CONFIG = Path.home() / ".hellollm" / "config.json"
MAX_READ_BYTES = 200_000
HOOK_TIMEOUT_SECONDS = 5
MAX_HOOK_OUTPUT_CHARS = 100_000

SYSTEM_PROMPT = """你是一个最小 Coding Agent。
你可以读取、写入和定点编辑当前工作区中的文本文件。
规则：
1. 需要知道文件真实内容时先调用 read_file，不要猜。
2. 修改局部内容优先 edit_file；完整创建/覆盖用 write_file。
3. 工具失败时阅读 tool_result，调整参数后重试。
4. 不要虚构已经执行过的工具结果。
5. 最终用简洁中文汇报你实际完成了什么。
"""

'''
  【详细说明：@dataclass】

  1. @dataclass 是什么？
     @dataclass 是 Python 标准库 dataclasses 提供的“类装饰器”。
     它不会把 class 变成另一种语言类型，而是在 class 定义完成后，
     根据字段声明自动生成一批重复性很高的样板方法。

     对下面的 ModelConfig：

         @dataclass
         class ModelConfig:
             api_key: str
             api_base: str
             model: str
             timeout: int = 120

     可以近似理解为 Python 自动帮我们补出：

         class ModelConfig:
             def __init__(self, api_key, api_base, model, timeout=120):
                 self.api_key = api_key
                 self.api_base = api_base
                 self.model = model
                 self.timeout = timeout

             def __repr__(self): ...
             def __eq__(self, other): ...

     因此：
         cfg = ModelConfig(api_key='sk-...', api_base='...', model='...')

     仍然是在实例化一个普通 Python 对象，只是 __init__、__repr__、
     __eq__ 等样板代码由 @dataclass 自动生成。

  2. 它解决什么问题？
     当一个类的主要职责是“承载一组相关数据”时，如果手工编写构造函数、
     字段赋值、repr、比较逻辑，会产生大量重复代码。

     本文件中的 ModelConfig 就是典型例子：
         api_key  -> Provider 凭据
         api_base -> API Endpoint
         model    -> 模型标识
         timeout  -> 请求超时

     load_model_config() 先从 JSON/CLI 收集零散配置，再统一包装成 ModelConfig，
     后续 stream_chat() 只接收一个 cfg 对象，而不是到处传 4 个独立参数。

  3. 是否类似 C/C++ struct？
     “用途上”可以类比：都是把多个相关字段组织成一个对象。
     但不能完全等同。

         C struct                -> 语言级数据聚合结构
         Python @dataclass class -> 普通 class + 自动生成数据相关样板方法

  4. dataclass 是否禁止成员方法？
     完全不禁止。它仍然是普通 class，可以定义任意实例方法、类方法、静态方法。

     例如：
         @dataclass
         class ModelConfig:
             api_key: str
             api_base: str

             def endpoint(self) -> str:
                 return self.api_base.rstrip('/') + '/chat/completions'

     这是合法的。

     所以 dataclass 的含义不是“纯数据、禁止行为”，而是：
         “这个类以数据字段为中心，请自动帮我生成常用样板方法。”

  5. 为什么这里适合 dataclass？
     ModelConfig 只是 Agent Runtime 的模型连接配置对象；
     它不负责 HTTP 请求本身，也没有复杂生命周期，因此用 dataclass 很合适。

  记忆：
      ModelConfig = 有明确字段类型的配置对象
      @dataclass  = 自动生成构造/显示/比较等样板代码
'''
@dataclass
class ModelConfig:
    """OpenAI-compatible Chat Completions 配置。"""

    api_key: str
    api_base: str
    model: str
    timeout: int = 120


def load_model_config(args: argparse.Namespace) -> ModelConfig:
    """读取 ~/.hellollm/config.json，再由命令行参数覆盖。"""
    data: dict[str, Any] = {}
    config_path = Path(args.config).expanduser()

    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise SystemExit(f"[配置错误] 无法读取 {config_path}: {e}")

    api_key = args.api_key or data.get("api_key")
    api_base = args.api_base or data.get("api_base") or "https://api.deepseek.com"
    model = args.model or data.get("model") or "deepseek-v4-flash"
    timeout = args.timeout or int(data.get("timeout", 120))

    if not api_key:
        raise SystemExit(
            "[配置错误] 缺少 api_key。\n"
            f"请创建 {config_path}，例如：\n"
            '{"api_key":"sk-...","api_base":"https://api.deepseek.com",'
            '"model":"deepseek-v4-flash","timeout":120}'
        )

    return ModelConfig(
        api_key=str(api_key),
        api_base=str(api_base).rstrip("/"),
        model=str(model),
        timeout=int(timeout),
    )


# ============================================================================
# 1. Tool Schema：给模型看的“可行动能力说明”
# ============================================================================

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取当前工作区内的文本文件。需要知道文件真实内容时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对工作区根目录的文件路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "在当前工作区内创建或完整覆盖文本文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "把文件中第一处完全匹配的 old_string 替换为 new_string。",
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


# ============================================================================
# 2. Tool Executor：真正产生本地副作用的执行层
# ============================================================================

def resolve_in_workspace(path: str, workspace_root: Path) -> Path:
    """
    把模型给出的路径限制在 workspace_root 内。

    注意：
    这是“执行层硬约束”，不是 Hook。
    安全边界应放在真正执行副作用的位置；Hook 只是额外控制切面。

    【详细说明：它究竟约束什么？】

    是，但要精确地说：
    本函数限制的是“文件类 Tool 可以解析并访问的文件系统路径范围”，
    不是完整 OS Sandbox，也不是所有命令/进程的 Execution Environment。

    本文件的 read_file / write_file / edit_file 都先经过它：

        Tool Handler
            ↓
        resolve_in_workspace(path, workspace_root)
            ↓
        确认最终真实路径仍位于 workspace_root
            ↓
        才真正 read/write/edit

    真实业务例子：
        workspace_root = /Users/me/project
        path = src/app.py

    解析后得到：
        /Users/me/project/src/app.py

    该路径仍属于 workspace_root，因此允许。

    如果模型提出：
        path = ../../.ssh/id_rsa

    resolve() 后可能得到：
        /Users/me/.ssh/id_rsa

    p.relative_to(root) 会失败，于是抛 PermissionError；
    真正的 read/write 根本不会发生。

    关键点：
    1. Path.resolve() 会消解 .、.. 并得到规范化真实路径，
       比简单字符串前缀判断更可靠。
    2. 这只是 Filesystem Scope Guard。
       它不限制网络、CPU、内存、子进程、Shell、系统调用、容器权限等。

    因此生产 Agent 通常还要叠加 Permission Engine、Sandbox/Container、
    Network Policy、Secret Isolation 等机制。
    """
    root = workspace_root.resolve()
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = root / p
    p = p.resolve()

    try:
        p.relative_to(root)
    except ValueError:
        raise PermissionError(f"路径越界：{path} 不在工作区 {root} 内")

    return p


def read_file(path: str, workspace_root: Path) -> str:
    p = resolve_in_workspace(path, workspace_root)
    if not p.exists():
        return f"错误：文件不存在：{path}"
    if p.is_dir():
        return f"错误：{path} 是目录"

    data = p.read_bytes()
    if b"\x00" in data[:8192]:
        return f"错误：{path} 可能是二进制文件"

    text = data.decode("utf-8", "replace")
    if len(data) > MAX_READ_BYTES:
        text = text[:MAX_READ_BYTES] + "\n…（文件过大，已截断）"
    return text


def write_file(path: str, content: str, workspace_root: Path) -> str:
    p = resolve_in_workspace(path, workspace_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {len(content.encode('utf-8'))} 字节：{p.relative_to(workspace_root.resolve())}"


def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    workspace_root: Path,
) -> str:
    p = resolve_in_workspace(path, workspace_root)
    if not p.exists():
        return f"错误：文件不存在：{path}"

    text = p.read_text(encoding="utf-8")
    if old_string not in text:
        return "错误：未找到完全匹配的 old_string；请先重新读取文件核对。"

    count = text.count(old_string)
    p.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
    return f"已替换第 1/{count} 处匹配：{p.relative_to(workspace_root.resolve())}"


def execute_tool(name: str, arguments: dict[str, Any], workspace_root: Path) -> str:
    """
    Tool Registry + Executor 的最小合并版。

    工具异常不让 Agent Loop 崩溃，而是转成 tool_result 再回填模型。
    这就是 ReAct 中“Action -> Observation -> 下一步推理”的工程实现。
    """
    try:
        if name == "read_file":
            return read_file(
                path=str(arguments["path"]),
                workspace_root=workspace_root,
            )
        if name == "write_file":
            return write_file(
                path=str(arguments["path"]),
                content=str(arguments["content"]),
                workspace_root=workspace_root,
            )
        if name == "edit_file":
            return edit_file(
                path=str(arguments["path"]),
                old_string=str(arguments["old_string"]),
                new_string=str(arguments["new_string"]),
                workspace_root=workspace_root,
            )
        return f"错误：未知工具 {name}"
    except Exception as e:
        return f"错误：工具 {name} 执行失败：{type(e).__name__}: {e}"


# ============================================================================
# 3. Hook：Agent Runtime 的“工具前/后控制切面”
# ============================================================================

@dataclass(frozen=True)
class HookRule:
    """
    最小 Hook 规则。

    event:
        PreToolUse  = 工具执行前
        PostToolUse = 工具执行后
    matcher:
        空字符串 = 匹配所有工具；
        否则采用 S03 教学版同样的“工具名包含 matcher”逻辑。
    command:
        外部 hook 命令 argv。

    【详细说明：为什么 HookRule 是数据结构？】

    你的理解基本正确：HookRule 是把多个相关字段捆绑成一个有明确语义对象的数据类。
    但它不是“返回数据协议”，更准确地说是：
        Hook Runtime 的规则配置记录 / Rule Descriptor。

    一条 HookRule 描述：
        event   = 挂在哪个生命周期事件上？
        matcher = 匹配哪些工具？
        command = 匹配后启动哪个 Hook Handler？

    例如：
        HookRule(
            event='PreToolUse',
            matcher='write_file',
            command=(python, me, '--hook-worker', 'protect-write'),
        )

    这不是“立刻执行 protect-write”，而是在 Registry 中声明：
        当 Runtime 到达 PreToolUse，且工具匹配 write_file 时，
        调度 protect-write Handler。

    因此这里采用典型的 Data + Engine 分离：
        HookRule    = What / When / Which Handler
        HookManager = How：遍历、匹配、调度、合并结果

    类似：
        Firewall Rule + Firewall Engine
        HTTP Route Table + Router
        CI Job Definition + Scheduler

    这样规则可以很容易来自 JSON/YAML/数据库，而 HookManager 不需要为每条规则改代码。

    这里还有 @dataclass(frozen=True)：
    frozen=True 表示实例建立后不能直接重新给字段赋值，适合稳定的注册规则。
    注意它只是字段赋值层面的浅层不可变，不等于整个对象图绝对不可变。
    """

    event: str
    matcher: str
    command: tuple[str, ...]


def match_hook(rule: HookRule, tool_name: str) -> bool:
    """
    【USER 原判断】
    1. “判断 tool_name 是否被 HookManager::_rules 挂住/勾住；命中以后就进入审核。”
    2. “是否遇到生命周期节点、是否在 rules 中注册、是否通过审查，
       按顺序共同决定 Tool 是否执行。”

    【评价：第 1 条作为整体 Runtime 比喻基本正确；第 2 条把多个层次混到 match_hook() 里了】

    “被 Rule 勾住”可以作为宏观理解，但 match_hook() 这个函数自身只负责：
        rule.matcher  <->  tool_name
    的匹配判断。

    它不负责：
        - 判断当前是 PreToolUse 还是 PostToolUse；
        - 判断 Rule 是否已注册（rule 能被遍历到，说明已在 self.rules 中）；
        - 执行具体安全审核；
        - 返回 allow / deny；
        - 决定真实 Tool 最终是否执行。

    完整链路应拆成：

        Runtime 到达 Hook Point
            ↓
        HookManager 先用 rule.event 过滤生命周期位置
            ↓
        match_hook(rule, tool_name) 再过滤工具
            ↓
        命中 Rule
            ↓
        run_hook_command() 启动 Handler
            ↓
        Handler 审核 payload
            ↓
        allow / deny / updatedInput / error
            ↓
        Agent Loop 才决定是否进入 execute_tool()

    因而：

        “是否运行某条 Hook”
        = event 命中 AND matcher 命中

    而：

        “Tool 是否最终执行”
        = 所有适用 PreToolUse Hook 没有 deny
          AND 其他执行层约束也允许
          AND Agent Loop 最终调用 execute_tool()

    如果 tool_name 没匹配到任何 HookRule，
    在【这个教学版 Hook System】中确实不会运行对应 Hook Handler；
    但不能泛化成“该 Tool 不需要任何安全检查”，
    因为本文件还有 resolve_in_workspace() 的 Executor 硬约束，
    生产系统还可能存在 Permission Engine / Sandbox / Approval。
    S03 教学版 matcher 规则非常简单：

    规则 1：matcher 为空字符串
        -> 视为通配规则，匹配所有工具。

    规则 2：matcher 非空
        -> 只要 matcher 是 tool_name 的子字符串就匹配。

    对应表达式：
        not rule.matcher or rule.matcher in tool_name

    示例：
        matcher=''           tool='write_file' -> True（通配）
        matcher='write_file' tool='write_file' -> True
        matcher='write'      tool='write_file' -> True
        matcher='read_file'  tool='write_file' -> False

    真实业务链路：
        LLM 提出 write_file
            ↓
        HookManager 遍历 self.rules
            ↓
        先判断 event 是否属于当前生命周期点
            ↓
        再调用 match_hook(rule, 'write_file')
            ↓
        只有匹配规则才真正启动对应 Handler

    生产 Hook Runtime 往往还会支持 exact/glob/regex/tool category/path condition/
    permission scope/if expression 等更丰富的匹配机制。
    """
    return not rule.matcher or rule.matcher in tool_name


def build_hook_payload(
    tool_name: str,
    arguments: dict[str, Any],
    workspace_root: Path,
    tool_output: Optional[str] = None,
) -> dict[str, Any]:
    """
    Hook stdin JSON。

    S03 最小字段：tool_name / arguments / tool_output。
    这里额外加入 workspace_root，让“保护路径”这个真实业务 hook
    可以独立判断目标是否越过工作区。

    【详细说明：payload 是什么？】

    payload 不是 Python 或 HTTP 独有名词。工程上通常表示：
        一次消息、请求、事件或传输中真正承载业务信息的数据部分。

    常见翻译：有效载荷 / 业务载荷 / 消息正文。

    HTTP 中经常出现 payload，是因为：
        HTTP Request = method + URL + headers + body
        body 中真正要传给业务端的数据，常被称为 request payload。

    本函数不是 HTTP，而是父 Agent 进程 -> Hook 子进程的 IPC，
    但思想完全相同：

        Agent Runtime
            ↓
        build_hook_payload() 打包 Hook Event 上下文
            ↓
        json.dumps()
            ↓
        stdin
            ↓
        Hook Worker

    这里 payload: dict[str, Any] 是“一个 Python 字典”，不是字典列表。

    key/value 的真实业务含义：
        tool_name      -> str，LLM 已经提出的工具名，例如 write_file
        arguments      -> dict，工具输入参数
        workspace_root -> str，Hook 做路径/策略判断所需上下文
        tool_output    -> str，仅 PostToolUse 时存在，代表 Tool 已执行结果

    所以这里的 payload 更准确地叫：
        Hook Event Message / Hook Event Context。

    它不是为了让 LLM 选择工具而准备数据；
    到这里时 LLM 已经完成 Tool Selection。

    时序：
        LLM 产生 tool_call
            ↓
        Runtime 已有 tool_name + arguments
            ↓
        build_hook_payload()
            ↓
        为 Hook Handler 准备事件上下文
            ↓
        Handler 返回 allow / deny / updatedInput / ...
    """
    payload: dict[str, Any] = {
        "tool_name": tool_name,
        "arguments": arguments,
        "workspace_root": str(workspace_root.resolve()),
    }
    if tool_output is not None:
        payload["tool_output"] = tool_output
    return payload


def run_hook_command(command: tuple[str, ...], payload: dict[str, Any]) -> dict[str, Any]:
    """
    subprocess + stdin/stdout JSON：S03 Hook Runner 的单文件版本。

    与 S03 的差别：
    - S03 为了允许任意 shell 语法使用 shell=True；
    - 本例直接传 argv，不经过 shell，减少命令字符串注入面。

    故障策略：
    - 保留 S03 教学版的 fail-open：hook 自己失败时返回 decision=error；
    - HookManager 对 error 不做 deny。
    - 但真正的 Tool Executor 仍有 workspace 路径硬限制，所以安全边界不依赖 hook。

    【详细说明：subprocess.run() 在这里做什么？】

    subprocess 是 Python 标准库，用于创建并控制操作系统子进程。

    这里 subprocess.run(...) 不是执行 LLM 选择的真实 Tool；
    它启动的是独立 Hook Handler 进程。

    这个单文件为了模拟真实 Hook Runtime 的进程边界，会再次启动自己：
        python minimal_stream_hook_agent.py --hook-worker protect-write

    因而形成：
        父进程：Agent Runtime
        子进程：Hook Worker

    二者通过 stdin/stdout JSON 通信。

    参数逐项解释：
    1. list(command)
       command 是 argv，例如：
           ['/usr/bin/python3', 'minimal_stream_hook_agent.py',
            '--hook-worker', 'protect-write']
       因为没有 shell=True，不先经过 shell 解析 |、>、; 等元字符。

    2. input=json.dumps(payload, ...)
       把 dict 序列化为 JSON 字符串，并写到子进程 stdin。

    3. capture_output=True
       捕获 stdout/stderr：
           proc.stdout -> Hook 的机器可读 JSON 结果
           proc.stderr -> 错误或诊断信息

    4. text=True
       stdin/stdout/stderr 按 str 而不是 bytes 处理。

    5. timeout=HOOK_TIMEOUT_SECONDS
       最多等待 5 秒，避免坏 Hook 永久阻塞 Agent Loop。

    6. proc.returncode
       0 通常表示 Hook 成功；非 0 表示 Hook 子进程自身失败。

    需要纠正一点：workspace_root 并没有作为 subprocess.run(cwd=...) 使用。
    它只是 payload 中的业务上下文，供 Hook Worker 做路径判断。
    真要改变子进程工作目录必须显式传 cwd=workspace_root。

    整体链路：
        Agent Runtime -> command + payload -> subprocess Hook Worker
        -> stdout JSON -> HookManager 解析决策。
    """
    try:
        proc = subprocess.run(
            list(command),
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"decision": "error", "reason": f"hook 启动/超时失败：{e}"}

    if proc.returncode != 0:
        return {
            "decision": "error",
            "reason": f"hook 退出码 {proc.returncode}: {proc.stderr[:300]}",
        }

    if len(proc.stdout) > MAX_HOOK_OUTPUT_CHARS:
        return {"decision": "error", "reason": "hook stdout 超限"}

    try:
        out = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        return {"decision": "error", "reason": f"hook stdout 不是 JSON：{e}"}

    if not isinstance(out, dict):
        return {"decision": "error", "reason": "hook 输出必须是 JSON object"}

    return out


class HookManager:
    """
    对应 HelloLLM hooks/manager.py 的最小调度器。

    PRE：
        model tool_call
             ↓
        匹配 PreToolUse rules
             ↓
        deny ? ── yes ──> 不执行工具，把拒绝原因作为 tool_result 回给模型
             │
             no
             ↓
        updatedInput ? ──> 合并参数
             ↓
        真正执行 Tool

    POST：
        Tool 原始结果
             ↓
        匹配 PostToolUse rules
             ↓
        updatedOutput ? ──> 改写
             ↓
        最终 tool_result 回填模型

    为什么“回填模型”非常关键？
    Hook 的决定不是只给 UI 看。拒绝、改写、脱敏后的结果进入下一轮上下文，
    LLM 才能依据真实 Observation 改变下一步 Action。

    【详细说明：HookManager 是否只是空壳？】

    不是。它有自己的实例成员变量：
        self.rules

    __init__(rules) 把当前 Runtime 已注册的 HookRule 列表保存下来，
    因而 self.rules 就是这个教学版 Hook Runtime 的最小 Registry。

    default_hook_manager() 实际注册了：
        PreToolUse + write_file + protect-write
        PreToolUse + edit_file  + protect-write
        PostToolUse + read_file + redact-read

    Manager 的价值也不取决于“字段多不多”，而在于它集中承担调度行为：
        run_pre_tool()  -> 选规则、匹配、执行、deny、updatedInput、fail-open
        run_post_tool() -> 选规则、匹配、执行、updatedOutput、返回最终 Observation

    因此它在架构职责上更接近：
        Hook Registry + Dispatcher + Result Aggregator
    的极简合并版。

    生产 Hook Runtime 往往还会增加 priority、async、timeout policy、
    cancellation、plugin/session scope、telemetry、冲突合并策略等。
    """

    def __init__(self, rules: list[HookRule]):
        self.rules = rules

    def run_pre_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        workspace_root: Path,
    ) -> dict[str, Any]:
        """
        执行当前一次 Tool Call 对应的全部 PreToolUse Hook。

        参数：
            tool_name:
                LLM 已经选择的工具名，例如 write_file / edit_file / read_file。

            arguments:
                LLM 生成的工具参数，进入本函数前已从 JSON 字符串解析成 dict。
                例如：{'path': '.env', 'content': 'API_KEY=...'}

            workspace_root:
                当前 Agent 工作区根目录。
                在这里更准确地属于 Policy Context，而不是完整 Execution Environment；
                protect-write Hook 用它判断路径是否越界。

        返回：
            {'allow': bool, 'arguments': dict, 'reason': str | None}

        真实业务位置：
            LLM 已提出 Tool Action
                ↓
            run_pre_tool()
                ↓
            allow / deny / updatedInput
                ↓
            execute_tool()  ← 真正副作用到这里才发生

        因此本函数本身不执行真实 Tool；
        它执行的是 Tool 之前的 Hook Handler。
        """
        # ------------------------------------------------------------------
        # 代码块 1：复制参数，建立整条 PreToolUse 链中的“最终参数状态”。
        # 不直接修改原始 arguments；多个 Hook 可以按顺序继续修改 final_args。
        # ------------------------------------------------------------------
        final_args = dict(arguments)

        # ------------------------------------------------------------------
        # 代码块 2：遍历 Registry 中所有 HookRule。
        # ------------------------------------------------------------------
        for rule in self.rules:
            if rule.event != "PreToolUse" or not match_hook(rule, tool_name):
                # 两级过滤：生命周期位置 + 工具 matcher。
                #
                # rule.event != 'PreToolUse'
                #     -> 这条规则不属于当前 Hook Point，例如它是 PostToolUse。
                #
                # not match_hook(rule, tool_name)
                #     -> 生命周期点正确，但当前工具不匹配。
                #
                # 使用 OR：任意一个条件不满足，这条规则都不该运行。
                # continue 只是“跳过当前 rule，检查下一条 rule”，
                # 并不是“继续执行真实 Tool”。
                continue

            # ------------------------------------------------------------------
            # 代码块 4：执行匹配到的 Hook Handler，而不是执行真实 Tool。
            #
            # 例如这里启动 protect-write，让它返回：
            # allow / deny / updatedInput / error。
            # 真正 write_file() 会在 run_agent_turn() 后面的 execute_tool() 才执行。
            # ------------------------------------------------------------------
            result = run_hook_command(
                rule.command,
                build_hook_payload(tool_name, final_args, workspace_root),
            )

            # 代码块 5：把 Hook 决策写入 stderr 日志，避免污染模型 stdout 正文。
            print(
                f"\n[hook:PreToolUse] {tool_name} -> {result.get('decision', 'allow')}",
                file=sys.stderr,
                flush=True,
            )

            # 代码块 6：deny 是阻断型决策；一旦出现立即短路返回，真实 Tool 不会执行。
            if result.get("decision") == "deny":
                return {
                    "allow": False,
                    "arguments": final_args,
                    "reason": result.get("reason", "被 PreToolUse hook 拒绝"),
                }

            # 代码块 7：updatedInput 用于修改即将送给 Tool Executor 的参数；
            # 后续 Hook 会继续看到已经被前一个 Hook 改过的 final_args。
            # 多个 hook 串联时，后面的 hook 看到前面已经改写过的参数。
            updated = result.get("updatedInput")
            if isinstance(updated, dict):
                final_args.update(updated)

            # 代码块 8：decision=error 表示 Hook 自身故障，不等于业务 deny。
            # 本教学版采用 fail-open：记录 warning，但不因 Hook 故障自动阻断 Tool。
            # 高风险生产系统也可能选择 fail-closed。
            # decision=error：按 S03 的 fail-open 继续。
            if result.get("decision") == "error":
                print(
                    f"[hook warning] fail-open: {result.get('reason')}",
                    file=sys.stderr,
                    flush=True,
                )

        # 代码块 9：所有候选 Hook 均处理完毕且没有 deny，返回最终 allow + final_args。
        return {"allow": True, "arguments": final_args, "reason": None}

    def run_post_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_output: str,
        workspace_root: Path,
    ) -> str:
        """
        执行 Tool 完成后的 PostToolUse Hook 链。

        参数：
            tool_name:
                刚刚真实执行完成的工具名。
            arguments:
                Tool 最终使用的参数；它可能已经被 PreToolUse 修改过。
            tool_output:
                Tool Executor 产生的原始结果。
            workspace_root:
                当前工作区上下文，供 Post Hook 做策略判断。

        返回：
            str，经过全部 PostToolUse Hook 处理后的最终输出。

        真实业务时序：
            execute_tool()
                ↓
            raw tool_output
                ↓
            run_post_tool()
                ↓
            脱敏 / 增强 / 审计 / 标准化
                ↓
            final_output
                ↓
            role='tool' 回填 messages
                ↓
            下一轮 LLM Context

        关键边界：PostToolUse 发生时真实 Tool 副作用已经发生，
        因此它不能撤销已写入的文件；它主要治理“结果进入模型之前”的内容与控制信息。
        """
        # 代码块 1：保留原始 tool_output，并建立可被多个 Post Hook 连续改写的 final_output。
        final_output = tool_output

        # 代码块 2：遍历所有 HookRule。
        for rule in self.rules:
            # 代码块 3：只选择 PostToolUse 且 matcher 匹配当前工具的规则。
            if rule.event != "PostToolUse" or not match_hook(rule, tool_name):
                continue

            # 代码块 4：执行 Post Hook Handler。
            # build_hook_payload() 此时会附带 tool_output=final_output；
            # 后一个 Post Hook 会看到前一个 Hook 已处理后的输出，形成串联管线。
            result = run_hook_command(
                rule.command,
                build_hook_payload(
                    tool_name,
                    arguments,
                    workspace_root,
                    tool_output=final_output,
                ),
            )

            # 代码块 5：记录 Post Hook 决策日志。
            print(
                f"\n[hook:PostToolUse] {tool_name} -> {result.get('decision', 'allow')}",
                file=sys.stderr,
                flush=True,
            )

            # 代码块 6：读取 updatedOutput；如果 Hook 提供新输出，就替换 final_output。
            # 例如 read_file 读到 api_key=sk-xxx，可在此脱敏成 <REDACTED> 后再进入 LLM。
            updated = result.get("updatedOutput")
            if updated is not None:
                final_output = str(updated)

            # 代码块 7：Post Hook 自身 error 时按 fail-open 保留当前 final_output 并继续。
            if result.get("decision") == "error":
                print(
                    f"[hook warning] fail-open: {result.get('reason')}",
                    file=sys.stderr,
                    flush=True,
                )

        # 代码块 8：返回最终 Observation；它稍后会作为 role='tool' content 回填模型。
        return final_output


# ============================================================================
# 4. 同一 Python 文件兼任 Hook Worker
# ============================================================================

SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
}

SECRET_LINE_RE = re.compile(
    r"""(?ix)
    \b(
        api[_-]?key |
        access[_-]?token |
        auth[_-]?token |
        password |
        passwd |
        secret
    )\b
    \s*[:=]\s*
    ([^\s,;]+)
    """
)
TOKEN_RE = re.compile(r"\b(?:sk|ghp|glpat|xox[baprs])[-_A-Za-z0-9]{12,}\b")


def hook_worker_protect_write(payload: dict[str, Any]) -> dict[str, Any]:
    """
    真实业务示例 A：PreToolUse 写入保护。

    用途：
    - 禁止模型写 .git；
    - 禁止直接覆盖常见凭据/私钥文件；
    - 禁止工作区外路径。

    这类 Hook 适合做：
    permission policy / compliance / protected-path guard / approval gate。
    """
    args = dict(payload.get("arguments") or {})
    path_text = str(args.get("path", ""))
    root = Path(str(payload["workspace_root"])).resolve()

    p = Path(path_text).expanduser()
    if not p.is_absolute():
        p = root / p
    p = p.resolve()

    try:
        # 【USER 原判断】“规定执行的路径”
        # 【评价：部分正确，但这里不是设置/规定 cwd，而是验证目标路径是否仍在 workspace 内】
        #
        # p.relative_to(root) 不会修改 p，也不会改变子进程工作目录。
        # 它尝试计算：
        #     rel = p 相对于 root 的路径
        #
        # 若 p 位于 root 内：
        #     root=/project, p=/project/src/a.py -> rel=src/a.py
        #
        # 若 p 已经越出 root：
        #     relative_to() 抛 ValueError
        #     -> except 分支返回 decision="deny"
        #
        # 所以这一行的准确职责是：
        #     Filesystem Containment Check / Workspace Boundary Check
        rel = p.relative_to(root)
    except ValueError:
        return {
            "decision": "deny",
            "reason": f"禁止写出工作区：{path_text}",
        }

    if ".git" in rel.parts:
        # 处理受保护分支 rel.parts
        return {
            "decision": "deny",
            "reason": "禁止通过普通文件工具直接修改 .git 内部数据",
        }

    if p.name in SENSITIVE_NAMES or p.suffix.lower() in SENSITIVE_SUFFIXES:
        # 【补全说明】这里是两套规则取 OR：
        # 1) p.name 命中完整敏感文件名，如 .env / id_rsa
        # 2) p.suffix 命中敏感扩展名，如 .pem / .key / .p12 / .pfx
        # 不能说这些目标“都在 SENSITIVE_SUFFIXES 中”。
        return {
            "decision": "deny",
            "reason": f"受保护的敏感文件：{rel}",
        }

    # 【USER 原判断】
    # “运行到这里说明 Hook 判断受保护内容安全，允许 LLM 建议的写入 Tool 通过。”
    #
    # 【评价：基本正确，但必须限定‘安全’和‘通过’的范围】
    #
    # 到这里仅表示 protect-write Handler 当前实现的几项策略均通过：
    #   - 未越出 workspace_root
    #   - 不在 .git 内
    #   - 文件名不在 SENSITIVE_NAMES
    #   - 扩展名不在 SENSITIVE_SUFFIXES
    #
    # 它不能证明“写入内容本身完全安全”，也不能代表所有系统安全层都通过。
    #
    # 另外 decision="allow" 只是把允许结果返回父 Runtime；
    # 真正 write_file/edit_file 仍要等：
    #   Hook Worker -> HookManager.run_pre_tool() -> Agent Loop -> execute_tool()
    # 才会发生。
    # 这里演示 updatedInput：把 "./a/../b.txt" 之类路径正规化成工作区相对路径。
    normalized = str(rel)
    return {
        "decision": "allow",
        "updatedInput": {"path": normalized},
    }


def hook_worker_redact_read(payload: dict[str, Any]) -> dict[str, Any]:
    """
    真实业务示例 B：PostToolUse 输出脱敏。

    工具已经读取了文件，但在内容进入 LLM Context 之前遮盖常见 secret。
    这正是 PostToolUse 的典型位置：
        Tool 原始输出 != 必须直接进入模型的最终 Observation。
    """
    output = str(payload.get("tool_output", ""))

    def repl(m: re.Match[str]) -> str:
        return f"{m.group(1)}=<REDACTED_BY_POST_TOOL_HOOK>"

    output = SECRET_LINE_RE.sub(repl, output)
    output = TOKEN_RE.sub("<REDACTED_TOKEN>", output)

    return {
        "decision": "allow",
        "updatedOutput": output,
    }


def run_hook_worker(name: str) -> int:
    """
    Hook 子进程入口。
    stdin 读 JSON，stdout 只打印一个 JSON object，便于父进程稳定解析。

    【USER 原判断】“run_hook_worker() 用于启动 Hook 级别的子进程？”

    【评价：不准确】
    真正创建 OS 子进程的是父进程中的 run_hook_command()：

        subprocess.run(...)

    run_hook_worker() 是【子进程已经创建以后】执行的入口/分发器。

    调用链：

        Parent Agent Process
            ↓ run_hook_command()
        subprocess.run(...)
            ↓ 创建
        Hook Child Process
            ↓ main()
        args.hook_worker 有值
            ↓
        run_hook_worker(name)

    因此：
        run_hook_command() = Parent-side Process Launcher
        run_hook_worker()  = Child-side Worker Entry / Dispatcher
    """
    try:
        payload = json.loads(sys.stdin.read() or "{}")

        if name == "protect-write":
            # 【USER 判断：基本正确】
            # name=="protect-write" 时分发到写入保护 Handler。
            # 但这里不再根据 Tool 类型做 matcher；Tool 类型筛选已在父进程 HookManager 完成。
            # 之所以进入此分支，是因为 HookRule.command 明确传入：
            #     --hook-worker protect-write
            # Handler 随后使用 payload.arguments/path/workspace_root 做具体策略审核。
            result = hook_worker_protect_write(payload)
        elif name == "redact-read":
            # 【USER 判断：部分正确】
            # 这里分发到 redact-read Handler，但它不是“读取前的授权判断”。
            # 这是 PostToolUse：read_file 已经执行完毕。
            # 它处理 payload["tool_output"]，把 API key/token/password 等敏感值脱敏，
            # 再通过 updatedOutput 返回给父 Runtime。
            result = hook_worker_redact_read(payload)
        else:
            # 【USER 判断：正确，补全语义】
            # worker 名称不属于当前程序支持的 Handler，返回 Hook 自身 error。
            # 这不是业务 deny；父 Runtime 会按本教学版 fail-open 策略处理。
            result = {"decision": "error", "reason": f"未知 hook worker: {name}"}

        sys.stdout.write(json.dumps(result, ensure_ascii=False))
        sys.stdout.flush()
        return 0
    except Exception as e:
        # hook runner 将非 0 退出码视作 hook 自身错误；主 Agent 按 fail-open 处理。
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 2


def default_hook_manager() -> HookManager:
    """
    仍然只有一个 .py 文件：
    主进程启动“自己”作为独立 Hook subprocess。

    【USER 原判断】
      “该函数用于注册默认 HookRule，即初始化 HookManager.rules。”

    【评价：正确】

    更完整调用关系：
        default_hook_manager()
            ↓ 构造 list[HookRule]
        HookManager(rules=[...])
            ↓ __init__()
        self.rules = rules

    本最小示例没有单独 register() API，
    因此所谓“注册”就是在构造 HookManager 时一次性注入默认 rules。
    """
    me = str(Path(__file__).resolve())
    python = sys.executable

    return HookManager(
        rules=[
            HookRule(
                # 【USER 原判断】
                # “PreToolUse 先匹配 matcher；命中后再由 command 做保护/审核；
                #  未匹配则不审核，匹配且安全则继续，否则停止。”
                #
                # 【评价：总体基本正确，但 command 的角色需要纠正】
                #
                # event="PreToolUse"
                #   = 生命周期过滤：这条 Rule 只在 Tool 真正执行前参与。
                #
                # matcher="write_file"
                #   = Tool 选择条件：当前 tool_name 命中 write_file 时 Rule 才适用。
                #
                # command=(...)
                #   = Handler 的“启动 argv / 执行描述”，不是审核条件本身。
                #
                # protect-write Handler
                #   = 真正执行 workspace/.git/敏感文件名等具体策略审核。
                #
                # 严格顺序：
                #   PreToolUse -> event 过滤 -> matcher 命中
                #   -> run_hook_command(command, payload)
                #   -> protect-write -> allow/deny/updatedInput
                #
                # matcher 未命中仅表示【这条 Rule】不执行，
                # 不能泛化成“该 Tool 完全不受其他安全层检查”。
                event="PreToolUse",
                matcher="write_file",
                command=(python, me, "--hook-worker", "protect-write"),
            ),
            HookRule(
                # 【USER 原判断】
                # “PreToolUse 节点先匹配 edit_file，匹配后进行安全审查。”
                #
                # 【评价：正确；补全 Handler 复用关系】
                #
                # write_file 与 edit_file 是两个不同 matcher 的 Rule，
                # 但共用同一个 protect-write Handler，
                # 因为二者都有文件写副作用并需要相同路径保护策略。
                #
                # 即：
                #   write_file ─┐
                #               ├─> protect-write
                #   edit_file ──┘
                event="PreToolUse",
                matcher="edit_file",
                command=(python, me, "--hook-worker", "protect-write"),
            ),
            HookRule(
                # 【USER 原判断 1】
                # “PostToolUse 命中 read_file 后进行相关安全性/审查。”
                #
                # 【评价：前半正确，但这里更准确叫‘结果脱敏/输出治理’】
                #
                # read_file 到达 PostToolUse 时已经执行完毕；
                # 本 Rule 不决定“能不能读”，而是在结果进入 LLM Context 前：
                #   raw tool_output -> redact-read -> updatedOutput -> LLM
                #
                # 【USER 问题 2】command 参数分别是什么？
                #
                # command=(python, me, "--hook-worker", "redact-read")
                #
                # python
                #   = sys.executable
                #   = 当前运行主 Agent 的 Python 解释器路径。
                #
                # me
                #   = 当前这个 .py 文件自身的绝对路径。
                #
                # "--hook-worker"
                #   = 内部 CLI 开关；main() 看到它后进入 Hook Worker 模式。
                #
                # "redact-read"
                #   = Worker 名称；
                #     run_hook_worker("redact-read") 据此分发到
                #     hook_worker_redact_read(payload)。
                #
                # 等价于执行：
                #   <python> <this_file.py> --hook-worker redact-read
                #
                # payload 不在 argv 中，而是通过 subprocess.run(input=...)
                # 从父进程 stdin 发送给子进程。
                event="PostToolUse",
                matcher="read_file",
                command=(python, me, "--hook-worker", "redact-read"),
            ),
        ]
    )


# ============================================================================
# 5. Model Stream：OpenAI-compatible SSE，零第三方依赖
# ============================================================================

class ModelError(RuntimeError):
    pass


def stream_chat(
    messages: list[dict[str, Any]],
    cfg: ModelConfig,
) -> Iterator[dict[str, Any]]:
    """
    真正流式请求：
        POST {api_base}/chat/completions
        "stream": true

    每收到一个 SSE data: 块就立刻 yield，不先聚合完整答案。

    只暴露两类我们需要的增量：
    - text_delta
    - tool_call_delta

    某些推理模型还会返回 reasoning_content。
    本最小示例不把隐藏推理过程打印到终端，也不需要它来执行 Tool Loop。
    """
    payload = {
        "model": cfg.model,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "stream": True,
    }

    request = urllib.request.Request(
        f"{cfg.api_base}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    try:
        response = urllib.request.urlopen(request, timeout=cfg.timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:1000]
        raise ModelError(f"HTTP {e.code}: {body}") from e
    except (urllib.error.URLError, OSError) as e:
        if isinstance(e, (socket.timeout, TimeoutError)):
            raise ModelError(f"请求超时（>{cfg.timeout}s）") from e
        raise ModelError(f"网络错误：{getattr(e, 'reason', e)}") from e

    with response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue

            data = line[5:].strip()
            if data == "[DONE]":
                break

            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue

            choices = event.get("choices") or []
            if not choices:
                continue

            delta = choices[0].get("delta") or {}

            if delta.get("content"):
                yield {
                    "type": "text_delta",
                    "text": str(delta["content"]),
                }

            for tc in delta.get("tool_calls") or []:
                fn = tc.get("function") or {}
                yield {
                    "type": "tool_call_delta",
                    "index": int(tc.get("index", 0)),
                    "id": str(tc.get("id") or ""),
                    "name": str(fn.get("name") or ""),
                    "arguments": str(fn.get("arguments") or ""),
                }


def consume_model_stream(
    messages: list[dict[str, Any]],
    cfg: ModelConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    一边把 text_delta 立即打印给用户，一边只为协议需要聚合 tool_call delta。

    OpenAI-compatible stream 中，一个 function call 的 arguments 通常会被拆成很多块：
        {"path":
        "README"
        ".md"}
    所以必须按 index 累积后才能 json.loads。
    """
    text_parts: list[str] = []
    pending: dict[int, dict[str, str]] = {}

    for event in stream_chat(messages, cfg):
        if event["type"] == "text_delta":
            text = event["text"]
            text_parts.append(text)
            print(text, end="", flush=True)

        elif event["type"] == "tool_call_delta":
            index = event["index"]
            slot = pending.setdefault(
                index,
                {"id": "", "name": "", "arguments": ""},
            )
            if event["id"]:
                slot["id"] += event["id"]
            if event["name"]:
                slot["name"] += event["name"]
            slot["arguments"] += event["arguments"]

    tool_calls: list[dict[str, Any]] = []
    for index in sorted(pending):
        tc = pending[index]
        tool_calls.append(
            {
                "id": tc["id"] or f"call_{index}",
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": tc["arguments"] or "{}",
                },
            }
        )

    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts) or None,
    }
    if tool_calls:
        assistant_message["tool_calls"] = tool_calls

    return assistant_message, tool_calls


# ============================================================================
# 6. Agent Loop：Model -> Hook -> Tool -> Hook -> Observation -> Model
# ============================================================================

def run_agent_turn(
    messages: list[dict[str, Any]],
    cfg: ModelConfig,
    workspace_root: Path,
    hooks: HookManager,
    max_turns: int,
) -> None:
    """
    单个用户回合内部可能有多次 LLM 调用。

    这就是论文/Claude Code 里最值得先理解的核心：
        while not stopped:
            action = model(...)
            if no tool:
                stop
            observation = execute_tool(...)
            history += action + observation

    S03 只在 execute_tool 前后增加了两个 Hook 扩展点。
    """
    for turn in range(1, max_turns + 1):
        print(
            f"\n[agent] turn {turn}/{max_turns} | model={cfg.model}",
            file=sys.stderr,
            flush=True,
        )

        assistant_message, tool_calls = consume_model_stream(messages, cfg)
        messages.append(assistant_message)

        # 主要停止条件：模型本轮没有请求工具。
        if not tool_calls:
            print()
            return

        # 如果模型一边输出文本一边调用工具，工具状态另走 stderr，避免破坏正文流。
        print(file=sys.stderr)

        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            raw_args = tc["function"]["arguments"]

            try:
                arguments = json.loads(raw_args)
                if not isinstance(arguments, dict):
                    # 【详细说明：为什么 JSON 能解析成功还要检查 dict？】
                    #
                    # 是的：这里检查的是“LLM 输出是否符合 Function Tool Arguments 协议”。
                    # json.loads() 能解析很多合法 JSON：
                    #     {'path':'README.md'} -> dict（协议需要）
                    #     ['README.md']        -> list（JSON 合法，但 Tool 参数协议不合格）
                    #     'README.md'          -> str（JSON 合法，但协议不合格）
                    #     123                  -> int（JSON 合法，但协议不合格）
                    #
                    # 本文件 Tool Schema 的 parameters 顶层都是 type='object'，
                    # 因而最终必须得到 dict。
                    #
                    # 为什么不在 consume_model_stream() 处理？
                    # 因为职责不同：
                    #
                    # consume_model_stream()
                    #     = Streaming / Protocol Assembly 层
                    #     只负责收集 text_delta/tool_call_delta，并把碎片拼成完整 Tool Call。
                    #
                    # run_agent_turn()
                    #     = Agent Loop / Tool Orchestration 层
                    #     马上要进入 Hook/Permission/Executor，
                    #     所以这里才负责把模型字符串转换成“可执行的业务参数对象”。
                    #
                    # 还有一个业务好处：参数非法时，本层可以生成 role='tool' 的错误 Observation
                    # 回填给 LLM，让模型下一轮自行纠正，而不是让 Streaming Parser 直接终止整个回合。
                    raise ValueError("工具 arguments 必须是 JSON object")
            except (json.JSONDecodeError, ValueError) as e:
                result = f"错误：工具参数 JSON 无效：{e}; raw={raw_args[:300]}"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    }
                )
                continue

            print(
                f"[tool proposed] {tool_name}({json.dumps(arguments, ensure_ascii=False)[:240]})",
                file=sys.stderr,
                flush=True,
            )

            # ----------------------------------------------------------------
            # Hook Point 1: PreToolUse
            # ----------------------------------------------------------------
            pre = hooks.run_pre_tool(
                # 【USER 原判断】
                # “tool_name 经 PreToolUse 审查：
                #  ① 是否有对应注册事件；
                #  ② tool_name 是否匹配；
                #  ③ arguments/workspace_root 是否符合安全要求。”
                #
                # 【评价：整体链路基本正确，但三步属于不同层】
                #
                # Agent Loop 对每个解析成功的 Tool Call 都调用 run_pre_tool()。
                # 进入 run_pre_tool() 后：
                #
                #   ① rule.event == "PreToolUse"
                #      -> 生命周期 Rule 过滤
                #
                #   ② match_hook(rule, tool_name)
                #      -> Tool 名称匹配
                #
                #   ③ 命中后 build_hook_payload(...)
                #      -> 把 arguments + workspace_root 交给 Handler
                #
                #   ④ Handler 自己决定实际检查哪些字段
                #      protect-write 才真正检查 path/.git/sensitive name。
                #
                # 所以：
                #   event + matcher = Hook Selection
                #   Handler         = Hook Policy Logic
                tool_name=tool_name,          # LLM 准备调用哪个 Tool，例如 write_file
                arguments=arguments,          # 已从 JSON 解码的 Tool Input 参数 dict
                workspace_root=workspace_root,# Workspace Policy Context；主要用于路径边界判断，不等同完整 Execution Environment
            )

            if not pre["allow"]:
                # 关键：拒绝不是让整个 Agent 崩掉。
                # 它成为 Observation 回给模型，模型下一轮可选择别的路径。
                result = f"HOOK_DENIED: {pre['reason']}"
                final_arguments = pre["arguments"]
                print(
                    f"[tool blocked] {tool_name}: {pre['reason']}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                final_arguments = pre["arguments"]

                # 真正的副作用发生在这里。
                raw_result = execute_tool(
                    tool_name,
                    final_arguments,
                    workspace_root,
                )

                # ------------------------------------------------------------
                # Hook Point 2: PostToolUse
                # ------------------------------------------------------------
                result = hooks.run_post_tool(
                    # 【USER 原判断】
                    # “Tool 已执行完成；执行结果还要进一步核查，然后回填 LLM。”
                    #
                    # 【评价：正确，这是 PostToolUse 最关键的时序理解】
                    #
                    # 此时 execute_tool() 已完成，raw_result 已产生。
                    # PostToolUse 不能撤销已经发生的真实 Tool 副作用；
                    # 它治理的是 Tool Result 成为下一轮 LLM Observation 之前的阶段：
                    #
                    #   raw_result
                    #       ↓
                    #   PostToolUse
                    #       ↓
                    #   脱敏 / 标准化 / 审计 / 增强
                    #       ↓
                    #   result
                    #       ↓
                    #   messages.append(role="tool")
                    #
                    # arguments 使用 final_arguments，
                    # 因为 PreToolUse 可能修改过参数；
                    # PostToolUse 应看到 Tool 真正执行时使用的参数。
                    tool_name=tool_name,
                    arguments=final_arguments,
                    tool_output=raw_result,
                    workspace_root=workspace_root,
                )

                print(
                    f"[tool done] {tool_name} -> {len(result)} chars",
                    file=sys.stderr,
                    flush=True,
                )

            # 最终 Observation 回填模型。
            # 注意是“经过 PostToolUse 后的 result”，不是原始工具输出。
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )

    print(
        f"\n[agent] 达到最大工具循环轮数 {max_turns}，本回合停止。",
        file=sys.stderr,
    )


# ============================================================================
# 7. CLI / REPL
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="单文件 Stream + Hook 最小 Coding Agent"
    )
    parser.add_argument("-p", "--prompt", help="无头单次提示词；不传则进入 REPL")
    parser.add_argument(
        "--root",
        default=".",
        help="Agent 可访问的工作区根目录，默认当前目录",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"配置文件，默认 {DEFAULT_CONFIG}",
    )
    parser.add_argument("--api-key")
    parser.add_argument("--api-base")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--max-turns", type=int, default=8)

    # 内部模式：同一文件被父 Agent 作为 hook subprocess 启动。
    parser.add_argument("--hook-worker", help=argparse.SUPPRESS)

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Hook 子进程必须最先分流；它不需要 API key，也不能再进入 Agent。
    if args.hook_worker:
        return run_hook_worker(args.hook_worker)

    cfg = load_model_config(args)
    workspace_root = Path(args.root).expanduser().resolve()
    if not workspace_root.exists() or not workspace_root.is_dir():
        raise SystemExit(f"[参数错误] --root 不是目录：{workspace_root}")

    hooks = default_hook_manager()

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
            + f"\n当前工作区根目录：{workspace_root}\n"
            + "文件工具的 path 请使用相对该根目录的路径。",
        }
    ]

    print(f"[workspace] {workspace_root}", file=sys.stderr)
    print("[mode] model streaming = ON; hooks = ON", file=sys.stderr)

    if args.prompt:
        messages.append({"role": "user", "content": args.prompt})
        run_agent_turn(
            messages,
            cfg,
            workspace_root,
            hooks,
            max_turns=args.max_turns,
        )
        return 0

    # 最小 REPL：同一 messages 列表持续保存上下文。
    while True:
        try:
            user_text = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user_text:
            continue
        if user_text in {"/exit", "/quit", "exit", "quit"}:
            return 0

        messages.append({"role": "user", "content": user_text})

        try:
            run_agent_turn(
                messages,
                cfg,
                workspace_root,
                hooks,
                max_turns=args.max_turns,
            )
        except ModelError as e:
            print(f"\n[模型错误] {e}", file=sys.stderr)
        except KeyboardInterrupt:
            print("\n[已中止本轮]", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
