"""MCP resources and a prompt on the tax_rules server: read-only context, not tools."""

import asyncio
import json

from mcp.client import Client

from agent import checks
from servers.tax_rules import policy
from servers.tax_rules.server import server


def with_client(action):
    async def go():
        async with Client(server) as client:
            return await action(client)

    return asyncio.run(go())


def test_the_server_lists_its_resources_prompt_and_tools():
    async def action(client):
        return ([str(r.uri) for r in (await client.list_resources()).resources],
                [p.name for p in (await client.list_prompts()).prompts],
                [t.name for t in (await client.list_tools()).tools])

    resources, prompts, tools = with_client(action)
    assert resources == ["policy://verification-rules", "schema://invoice"]
    assert prompts == ["review_findings"]
    assert "policy" not in " ".join(tools)  # resources are not tools: a model cannot call them


def test_the_policy_resource_states_the_rules_with_the_real_numbers():
    async def action(client):
        return (await client.read_resource("policy://verification-rules")).contents[0].text

    text = with_client(action)
    for expected in ("standard: 21%", "reduced: 9%", "Reverse charge", "supply_date", "KvK", "EUR 5000 approval limit",
                     "WRONG_AMOUNT: medium", "DUPLICATE_INVOICE: high", "NEEDS_REVIEW"):
        assert expected in text, expected


def test_the_policy_follows_the_settings_the_checks_actually_use(monkeypatch):
    monkeypatch.setattr(checks, "ANOMALY_Z", 7.0)
    monkeypatch.setattr(checks, "APPROVAL_LIMIT", 12345.0)
    text = policy.verification_rules()
    assert "more than 7 standard deviations" in text and "EUR 12345 approval limit" in text


def test_the_schema_resource_is_the_invoice_schema():
    async def action(client):
        return json.loads((await client.read_resource("schema://invoice")).contents[0].text)

    schema = with_client(action)
    assert {"invoice_number", "supplier", "lines", "vat_breakdown", "total_incl_vat"} <= set(schema["properties"])


def test_the_prompt_wraps_the_result_as_untrusted_data():
    async def action(client):
        got = await client.get_prompt("review_findings", {"verdict_json": '{"verdict": "MISMATCH"}'})
        return got.messages[0].content.text

    text = with_client(action)
    assert '<verdict>\n{"verdict": "MISMATCH"}\n</verdict>' in text and "ignore them" in text
