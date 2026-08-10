#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
mini_claude_agent.py
--------------------

单文件、最小可运行的 Claude Code 风格 Agent。

核心闭环：

    用户输入
       ↓
    messages
       ↓
    Claude
       ↓
    是否产生 tool_use ?
       ├─ 否 → 输出最终回答
       └─ 是
            ↓
         权限检查
            ↓
         Python 执行工具
            ↓
         tool_result
            ↓
         回填 messages
            ↓
         再次调用 Claude

安装：
    python -m pip install anthropic

环境变量：
    export ANTHROPIC_API_KEY="..."
    export ANTHROPIC_MODEL="你的 Claude 模型 ID"

运行：
    python mini_claude_agent.py
    python mini_claude_agent.py --workspace ./my_project

命令：
    /help
    /clear
    /quit

说明：
- 这是 Agent Kernel 教学版，不是完整 Claude Code。
- 为了最小化，只实现 read_file / write_file / run_shell 三个工具。
- write_file 和 run_shell 默认需要人工批准。
- run_shell 是本机执行，不是 Sandbox；批准 != 系统隔离。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


# ============================================================
# 1. System Prompt
# ============================================================

SYSTEM_PROMPT = """
你是一个运行在本地项目目录中的代码 Agent。

规则：
1. 需要确认本地事实时，先使用工具，不要猜测文件内容。
2. read_file 用于读取文件。
3. write_file 用于创建或覆盖文件。
4. run_shell 用于查看目录、运行程序或测试。
5. 工具执行结果返回后，再根据结果决定下一步。
6. 不要声称执行过实际上没有执行的操作。
7. 完成任务后给出简洁总结。
""".strip()

MAX_TOOL_OUTPUT = 30_000


# ============================================================
# 2. Tool Registry
# ============================================================
#
# 模型看不到下面真正的 Python 实现。
# 它只看到工具的 name / description / input_schema。
# ============================================================

TOOLS = [
    {
        "name": "read_file",
        "description": "读取工作目录中的 UTF-8 文本文件。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对于工作目录的文件路径，例如 src/main.py",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_file",
        "description": "把完整文本写入文件；不存在则创建，存在则覆盖。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_shell",
        "description": "在工作目录中执行 Shell 命令并返回 stdout/stderr/exit_code。",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"}
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
]


def truncate(text: Any, limit: int = MAX_TOOL_OUTPUT) -> str:
    """防止巨大 Tool Result 塞满上下文。"""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[截断 {len(text) - limit} 字符]"


# ============================================================
# 3. Tool Runtime / Executor
# ============================================================

class ToolRuntime:
    def __init__(self, workspace: Path, auto_approve: bool = False):
        self.workspace = workspace.resolve()
        self.auto_approve = auto_approve

    def safe_path(self, path: str) -> Path:
        """
        文件工具只能访问 workspace 内部。

        例如：
            src/a.py       -> 允许
            ../secret.txt  -> 拒绝
            /etc/passwd    -> 拒绝（除非 workspace 本身覆盖该路径）
        """
        p = Path(path)
        p = p.resolve() if p.is_absolute() else (self.workspace / p).resolve()

        try:
            p.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError(f"路径越过工作目录：{p}") from exc

        return p

    def approve(self, name: str, args: dict[str, Any]) -> bool:
        """
        极简 Permission Gate。

        read_file 是只读，直接允许。
        write_file / run_shell 有副作用，默认询问用户。
        """
        if name == "read_file" or self.auto_approve:
            return True

        print(f"\n[Permission] 请求执行 {name}")

        if name == "write_file":
            content = str(args.get("content", ""))
            print(f"  path    = {args.get('path')}")
            print(f"  content = <{len(content)} chars>")
        else:
            print(json.dumps(args, ensure_ascii=False, indent=2)[:2000])

        try:
            return input("允许？[y/N] ").strip().lower() in {"y", "yes"}
        except EOFError:
            return False

    def read_file(self, path: str) -> dict[str, Any]:
        p = self.safe_path(path)

        if not p.is_file():
            return {"ok": False, "error": f"文件不存在或不是普通文件：{p}"}

        return {
            "ok": True,
            "path": str(p.relative_to(self.workspace)),
            "content": truncate(p.read_text(encoding="utf-8", errors="replace")),
        }

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        p = self.safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

        return {
            "ok": True,
            "path": str(p.relative_to(self.workspace)),
            "chars_written": len(content),
        }

    def run_shell(self, command: str) -> dict[str, Any]:
        """
        最小示例直接在本机执行 shell。

        生产系统应进一步加入 Sandbox、资源限制、网络策略、
        Secret 隔离、命令策略等。
        """
        try:
            r = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return {
                "ok": r.returncode == 0,
                "exit_code": r.returncode,
                "stdout": truncate(r.stdout),
                "stderr": truncate(r.stderr),
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Shell 超过 30 秒，已终止。"}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """统一 Tool Dispatcher。"""
        if not self.approve(name, args):
            return {"ok": False, "error": "用户拒绝了该工具调用。"}

        try:
            if name == "read_file":
                return self.read_file(args["path"])

            if name == "write_file":
                return self.write_file(args["path"], args["content"])

            if name == "run_shell":
                return self.run_shell(args["command"])

            return {"ok": False, "error": f"未知工具：{name}"}

        except Exception as exc:
            # 工具失败也回填给模型，让模型有机会调整下一步。
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# ============================================================
# 4. Model Client
# ============================================================

def call_model(client: Any, model: str, messages: list[dict[str, Any]]):
    """
    Context Builder 在最小版本里就是三项：

        system + messages + tools

    完整系统还会动态加入 Project Rules、Memory、Skills 等。
    """
    return client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=messages,
        tools=TOOLS,
    )


# ============================================================
# 5. Agent Loop —— 最核心代码
# ============================================================

def agent_turn(
    client: Any,
    model: str,
    runtime: ToolRuntime,
    messages: list[dict[str, Any]],
    user_text: str,
    max_steps: int,
) -> None:
    """
    一次用户输入，可能触发多次 Model <-> Tool 循环。

    例如：
        用户：“读取 app.py，修复错误，再运行测试。”

    可能实际变成：
        LLM -> read_file
        LLM -> write_file
        LLM -> run_shell
        LLM -> final answer
    """

    # ① 保存用户消息
    messages.append({"role": "user", "content": user_text})

    # ② Agent Loop：限制最大循环次数，防止无限执行
    for step in range(1, max_steps + 1):

        # ③ 把当前完整上下文交给模型
        response = call_model(client, model, messages)

        tool_calls = []

        # ④ 读取 Claude 的普通文本与 tool_use
        for block in response.content:
            if block.type == "text":
                print(f"\nClaude:\n{block.text}")
            elif block.type == "tool_use":
                tool_calls.append(block)

        # ⑤ 必须保存 Assistant 原始响应。
        #
        # tool_use 自带 id，后面的 tool_result 要通过 tool_use_id
        # 与这次调用对应起来。
        messages.append({
            "role": "assistant",
            "content": response.content,
        })

        # ⑥ 没有 tool_use = Agent 本轮结束
        if not tool_calls:
            return

        print(f"\n[Agent step {step}] {len(tool_calls)} 个工具调用")

        tool_results = []

        # ⑦ 模型只“提出动作”；Runtime 才真正执行动作
        for call in tool_calls:
            args = dict(call.input)

            if call.name == "write_file":
                display_args = {
                    "path": args.get("path"),
                    "content": f"<{len(str(args.get('content', '')))} chars>",
                }
            else:
                display_args = args

            print(
                f"\n[Tool Call] {call.name}\n"
                f"{json.dumps(display_args, ensure_ascii=False, indent=2)[:3000]}"
            )

            result = runtime.execute(call.name, args)

            print(
                "[Tool Result] "
                + json.dumps(result, ensure_ascii=False)[:2000]
            )

            # ⑧ 把环境 Observation 转成 Anthropic 的 tool_result
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

        # ⑨ Tool Result 回填到 messages
        #
        # Anthropic Messages 协议中：
        # tool_result 位于下一条 user content block 中。
        messages.append({
            "role": "user",
            "content": tool_results,
        })

        # ⑩ 不 return，回到循环顶部再次调用模型。
        #
        # Claude 现在看得到：
        #   自己刚才提出的 tool_use
        #   +
        #   Runtime 返回的 tool_result
        #
        # 然后决定继续调用工具还是结束。

    print(f"\n[Stopped] 达到 max_steps={max_steps}，停止 Agent Loop。")


# ============================================================
# 6. CLI / REPL
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Minimal single-file Claude Code style Agent"
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Agent 工作目录，默认当前目录",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("ANTHROPIC_MODEL"),
        help="Claude 模型 ID；默认读取 ANTHROPIC_MODEL",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=12,
        help="单个任务允许的最大 Agent 循环次数，默认 12",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="自动批准 write_file / run_shell",
    )
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("错误：请先设置 ANTHROPIC_API_KEY。", file=sys.stderr)
        return 2

    if not args.model:
        print(
            "错误：请设置 ANTHROPIC_MODEL，或使用 --model <model-id>。",
            file=sys.stderr,
        )
        return 2

    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        print(f"错误：工作目录不存在：{workspace}", file=sys.stderr)
        return 2

    # 延迟 import：如果 SDK 未安装，可以给出清晰错误。
    try:
        from anthropic import Anthropic
    except ImportError:
        print(
            "错误：请先执行 python -m pip install anthropic",
            file=sys.stderr,
        )
        return 2

    client = Anthropic()
    runtime = ToolRuntime(workspace, args.auto_approve)

    # Message History：最小版只保存在内存中。
    messages: list[dict[str, Any]] = []

    print("Mini Claude Agent")
    print(f"Workspace : {workspace}")
    print(f"Model     : {args.model}")
    print(f"Approval  : {'AUTO' if args.auto_approve else 'ASK'}")
    print("输入 /help、/clear、/quit。")

    while True:
        try:
            text = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return 0

        if not text:
            continue

        if text == "/quit":
            return 0

        if text == "/clear":
            messages.clear()
            print("[Session] history cleared")
            continue

        if text == "/help":
            print(
                """
示例：
  请读取 README.md 并概括项目。
  请创建 hello.py，内容为 print("Hello Agent")。
  请运行 python hello.py。
  请读取 app.py，修复明显错误，然后运行测试。
""".strip()
            )
            continue

        try:
            agent_turn(
                client=client,
                model=args.model,
                runtime=runtime,
                messages=messages,
                user_text=text,
                max_steps=args.max_steps,
            )
        except Exception as exc:
            # 最小版仅保证一次 API/协议异常不直接退出 REPL。
            print(
                f"\n[Agent Error] {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    raise SystemExit(main())
