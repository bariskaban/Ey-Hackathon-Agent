"""The MCP <-> Ollama tool loop, with a scripted model."""

import asyncio
import json
from types import SimpleNamespace

from stubs import DirectGateway

from agent import loop


def tool_call(name, **arguments):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))


def reply(content="", calls=None):
    return SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))


class ScriptedLLM:
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    async def chat(self, messages, **kwargs):
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def run_loop(llm, gateway, role="verifier", max_steps=8):
    async def go():
        records, final = [], None
        async for item in loop.run_tool_loop(llm, gateway, role, [{"role": "user", "content": "check"}],
                                             invoice_id="INV-X", max_steps=max_steps):
            if isinstance(item, loop.ToolCallRecord):
                records.append(item)
            else:
                final = item
        return records, final

    return asyncio.run(go())


def test_tool_call_then_answer(inbox):
    llm = ScriptedLLM(reply("", [tool_call("get_vendor", name_or_vat_number="Nobody B.V.")]),
                      reply("The vendor is unknown."))
    records, final = run_loop(llm, DirectGateway())
    assert [r.tool for r in records] == ["get_vendor"] and records[0].result["ok"]
    assert final.final_text == "The vendor is unknown." and final.steps == 2 and not final.hit_step_limit
    tool_message = final.messages[2]
    assert tool_message["role"] == "tool" and tool_message["tool_name"] == "get_vendor"
    assert json.loads(tool_message["content"])["found"] is False
    assert final.messages[1]["tool_calls"][0]["function"]["arguments"] == {"name_or_vat_number": "Nobody B.V."}
    assert len(llm.seen[1]) == 3  # the second call saw the first call's result


def test_parallel_tool_calls_all_run(inbox):
    llm = ScriptedLLM(reply("", [tool_call("get_vendor", name_or_vat_number="a"),
                                 tool_call("lookup_invoice_number", vendor_id="V999", invoice_no="x")]),
                      reply("done"))
    records, final = run_loop(llm, DirectGateway())
    assert [r.tool for r in records] == ["get_vendor", "lookup_invoice_number"]
    assert not records[1].result["ok"]  # unknown vendor: an error the model can read, not a crash
    assert "unknown vendor_id" in json.loads(final.messages[3]["content"])["error"]


def test_refused_call_reaches_the_model_as_a_message(inbox):
    llm = ScriptedLLM(reply("", [tool_call("get_vendor", name_or_vat_number="x")]), reply("understood"))
    records, final = run_loop(llm, DirectGateway(), role="extractor")  # the extractor may not use transactions
    assert records[0].result["refused"]
    content = json.loads(final.messages[2]["content"])
    assert content["refused"] is True and "error" in content


def test_step_limit_stops_a_model_that_never_stops(inbox):
    llm = ScriptedLLM(reply("", [tool_call("get_vendor", name_or_vat_number="x")]))
    records, final = run_loop(llm, DirectGateway(), max_steps=3)
    assert len(records) == 3 and final.hit_step_limit and final.steps == 3


def test_long_results_are_cut(inbox):
    class Huge(DirectGateway):
        async def call(self, role, tool, args=None, invoice_id=None):
            return {"ok": True, "data": {"rows": ["x" * 100] * 500}, "error": None, "refused": False}

    llm = ScriptedLLM(reply("", [tool_call("find_transactions", vendor_id="V001")]), reply("ok"))
    _, final = run_loop(llm, Huge())
    content = final.messages[2]["content"]
    assert len(content) < loop.MAX_TOOL_RESULT_CHARS + 100 and "result cut" in content


def test_no_tools_needed_means_one_step(inbox):
    _, final = run_loop(ScriptedLLM(reply("Nothing more to check.")), DirectGateway())
    assert final.steps == 1 and final.tool_calls == [] and final.final_text == "Nothing more to check."
