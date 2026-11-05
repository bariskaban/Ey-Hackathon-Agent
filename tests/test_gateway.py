import asyncio
import json

import pytest

from agent.gateway import Gateway
from servers.common.audit import AuditLog, read_entries


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def audit(tmp_path):
    return AuditLog(tmp_path / "audit.jsonl")


def make_gateway(audit, **kwargs):
    return Gateway(audit=audit, server_names=["transactions"], **kwargs)


def test_allowed_call_is_routed_and_audited(audit, case):
    vat = case("clean_standard")["expected_invoice"]["supplier"]["vat_number"]

    async def scenario():
        async with make_gateway(audit) as gateway:
            return await gateway.call("verifier", "get_vendor", {"name_or_vat_number": vat}, invoice_id="INV-X")

    result = run(scenario())
    assert result["ok"] and result["data"]["found"] and result["server"] == "transactions"
    [entry] = read_entries(audit.path)
    assert entry["invoice_id"] == "INV-X" and entry["role"] == "verifier"
    assert entry["server"] == "transactions" and entry["tool"] == "get_vendor"
    assert entry["args"] == {"name_or_vat_number": vat}
    assert entry["result_size"] > 0 and entry["latency_ms"] >= 0 and not entry["refused"]
    assert "iban" not in json.dumps(result["data"]).replace("iban_masked", "").replace("iban_changed_at", "")


def test_role_without_access_is_refused_and_audited(audit):
    async def scenario():
        async with make_gateway(audit) as gateway:
            return await gateway.call("reporter", "get_vendor", {"name_or_vat_number": "x"}, invoice_id="INV-X")

    result = run(scenario())
    assert result["refused"] and not result["ok"] and "may not use server" in result["error"]
    [entry] = read_entries(audit.path)
    assert entry["refused"] is True and entry["rows_returned"] == 0


def test_unknown_tool_is_refused(audit):
    async def scenario():
        async with make_gateway(audit) as gateway:
            return await gateway.call("verifier", "run_sql", {"query": "DROP TABLE vendors"})

    result = run(scenario())
    assert result["refused"] and "unknown tool" in result["error"]


def test_unknown_role_is_a_programming_error(audit):
    async def scenario():
        async with make_gateway(audit) as gateway:
            await gateway.call("intern", "get_vendor", {})

    with pytest.raises(ValueError):
        run(scenario())


def test_call_budget_is_enforced_per_invoice(audit):
    async def scenario():
        async with make_gateway(audit, max_calls=2) as gateway:
            args = {"name_or_vat_number": "nobody"}
            first = await gateway.call("verifier", "get_vendor", args, invoice_id="INV-A")
            second = await gateway.call("verifier", "get_vendor", args, invoice_id="INV-A")
            third = await gateway.call("verifier", "get_vendor", args, invoice_id="INV-A")
            other_invoice = await gateway.call("verifier", "get_vendor", args, invoice_id="INV-B")
            return first, second, third, other_invoice

    first, second, third, other = run(scenario())
    assert first["ok"] and second["ok"]
    assert third["refused"] and "budget" in third["error"]
    assert other["ok"]  # budgets are per invoice
    assert [e["refused"] for e in read_entries(audit.path, invoice_id="INV-A")] == [False, False, True]


def test_row_budget_is_enforced(audit):
    with_rows = "V001"

    async def scenario():
        async with make_gateway(audit, max_rows=20) as gateway:
            first = await gateway.call("verifier", "find_transactions", {"vendor_id": with_rows}, invoice_id="INV-A")
            second = await gateway.call("verifier", "find_transactions", {"vendor_id": with_rows}, invoice_id="INV-A")
            return first, second

    first, second = run(scenario())
    assert first["ok"] and first["rows_returned"] == 20
    assert second["refused"] and "rows" in second["error"]


def test_tool_error_is_returned_not_raised(audit):
    async def scenario():
        async with make_gateway(audit) as gateway:
            return await gateway.call("verifier", "find_transactions", {"vendor_id": "V999"}, invoice_id="INV-A")

    result = run(scenario())
    assert not result["ok"] and not result["refused"] and "unknown vendor_id" in result["error"]
    assert read_entries(audit.path)[0]["error"] is True


def test_llm_sees_only_the_allowed_tools(audit):
    async def scenario():
        async with make_gateway(audit) as gateway:
            return gateway.llm_tools("verifier"), gateway.tool_names()

    tools, all_tools = run(scenario())
    names = {t["function"]["name"] for t in tools}
    assert names == {"get_vendor", "lookup_invoice_number", "find_transactions", "get_purchase_order",
                     "vendor_payment_summary"}  # convert_document appears once the documents server exists
    assert set(all_tools) == names
    schema = next(t for t in tools if t["function"]["name"] == "get_vendor")["function"]
    assert schema["parameters"]["required"] == ["name_or_vat_number"]
