"""The MCP <-> Ollama tool loop.

Ollama does not speak MCP, so the host connects them: the model asks for a tool, the call goes
through the gateway (allowlist, budget, audit), and the result goes back to the model as a
`tool` message. The loop stops when the model answers without asking for a tool, or after
`max_steps` rounds.

Everything the model reads from documents or tool results is data. The system prompt says so,
and the model only ever sees the tools its role allows.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from agent.gateway import Gateway
from agent.llm import LLM

MAX_STEPS = 8
MAX_TOOL_RESULT_CHARS = 4000  # long results are cut so they cannot flood the context window


@dataclass
class ToolCallRecord:
    tool: str
    args: dict
    result: dict  # the gateway's result dict


@dataclass
class LoopResult:
    messages: list[dict]
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    final_text: str = ""
    steps: int = 0
    hit_step_limit: bool = False


def _tool_message(name: str, result: dict) -> dict:
    """What the model sees for a tool call: the data, or why it was refused or failed."""
    if result["ok"]:
        payload: Any = result["data"]
    else:
        payload = {"error": result["error"], "refused": result["refused"]}
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + '... [result cut: ask for a narrower query]'
    return {"role": "tool", "tool_name": name, "content": text}


async def run_tool_loop(llm: LLM, gateway: Gateway, role: str, messages: list[dict], *,
                        invoice_id: str | None, max_steps: int = MAX_STEPS) -> AsyncIterator[ToolCallRecord | LoopResult]:
    """Run the loop. Yields a ToolCallRecord after every tool call (so a UI can show it live),
    then one final LoopResult."""
    tools = gateway.llm_tools(role)
    outcome = LoopResult(messages=list(messages))
    for step in range(1, max_steps + 1):
        outcome.steps = step
        response = await llm.chat(outcome.messages, tools=tools or None)
        message = response.message
        assistant: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        calls = message.tool_calls or []
        if calls:
            assistant["tool_calls"] = [{"function": {"name": c.function.name, "arguments": dict(c.function.arguments)}}
                                       for c in calls]
        outcome.messages.append(assistant)
        if not calls:
            outcome.final_text = (message.content or "").strip()
            yield outcome
            return
        for call in calls:
            name, args = call.function.name, dict(call.function.arguments)
            result = await gateway.call(role, name, args, invoice_id=invoice_id)
            record = ToolCallRecord(name, args, result)
            outcome.tool_calls.append(record)
            outcome.messages.append(_tool_message(name, result))
            yield record
    outcome.hit_step_limit = True
    yield outcome
