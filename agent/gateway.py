"""The MCP gateway: every tool call from the agent goes through `Gateway.call`.

For each call it
  1. checks the role's allowlist,
  2. checks and updates the invoice's query budget,
  3. routes the call to the right MCP server session, and
  4. writes an audit-log entry (also for refused calls).

Servers still mask their own sensitive fields, so a mistake here does not expose data.
"""

import json
import os
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Self

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agent.roles import ROLES
from servers.common.audit import AuditLog
from servers.common.config import ROOT
from servers.registry import built_servers

DEFAULT_MAX_CALLS = 40  # tool calls per invoice
DEFAULT_MAX_ROWS = 400  # rows returned per invoice, in total


@dataclass
class Budget:
    max_calls: int
    max_rows: int
    calls: int = 0
    rows: int = 0

    def exhausted(self) -> str | None:
        if self.calls >= self.max_calls:
            return f"query budget exhausted: {self.max_calls} tool calls per invoice"
        if self.rows >= self.max_rows:
            return f"query budget exhausted: {self.max_rows} rows per invoice"
        return None


@dataclass
class _ToolEntry:
    server: str
    session: ClientSession
    tool: Any


@dataclass
class Gateway:
    audit: AuditLog = field(default_factory=AuditLog)
    max_calls: int = DEFAULT_MAX_CALLS
    max_rows: int = DEFAULT_MAX_ROWS
    server_names: list[str] | None = None  # default: every server that is built

    def __post_init__(self):
        self._stack: AsyncExitStack | None = None
        self._tools: dict[str, _ToolEntry] = {}
        self._budgets: dict[str, Budget] = {}

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> "Gateway":
        self._stack = AsyncExitStack()
        env = {k: v for k, v in os.environ.items() if k.startswith("INVOICE_")}
        for name, info in built_servers().items():
            if self.server_names is not None and name not in self.server_names:
                continue
            params = StdioServerParameters(command=sys.executable, args=["-m", info.module],
                                           env=env, cwd=str(ROOT))
            read, write = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            for tool in (await session.list_tools()).tools:
                self._tools[tool.name] = _ToolEntry(name, session, tool)
        return self

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    # ------------------------------------------------------------------ introspection

    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    def server_of(self, tool: str) -> str | None:
        entry = self._tools.get(tool)
        return entry.server if entry else None

    def llm_tools(self, role: str) -> list[dict]:
        """The tools the model sees for a role, in OpenAI-style function format (used by Ollama)."""
        result = []
        for name in ROLES[role].llm_tools:
            entry = self._tools.get(name)
            if entry is None:
                continue
            result.append({"type": "function", "function": {
                "name": name,
                "description": entry.tool.description or "",
                "parameters": entry.tool.input_schema,
            }})
        return result

    def budget(self, invoice_id: str | None) -> Budget:
        key = invoice_id or "_none"
        if key not in self._budgets:
            self._budgets[key] = Budget(self.max_calls, self.max_rows)
        return self._budgets[key]

    # ------------------------------------------------------------------ the call

    async def call(self, role: str, tool: str, args: dict | None = None,
                   invoice_id: str | None = None) -> dict:
        """Call a tool as `role`. Always returns a dict: ok, data, error, refused, rows_returned."""
        args = args or {}
        started = time.perf_counter()
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        entry = self._tools.get(tool)
        server = entry.server if entry else None

        refusal = None
        if entry is None:
            refusal = f"unknown tool {tool!r}"
        elif server not in ROLES[role].servers:
            refusal = f"role {role!r} may not use server {server!r}"
        else:
            refusal = self.budget(invoice_id).exhausted()
        if refusal:
            return self._finish(role, tool, server, args, invoice_id, started, refused=True, reason=refusal)

        budget = self.budget(invoice_id)
        budget.calls += 1
        try:
            result = await entry.session.call_tool(tool, args)
        except Exception as exc:  # noqa: BLE001 - any transport/protocol failure becomes an audited error
            return self._finish(role, tool, server, args, invoice_id, started, error=f"{type(exc).__name__}: {exc}")

        text = "".join(block.text for block in result.content if getattr(block, "text", None))
        if result.is_error:
            return self._finish(role, tool, server, args, invoice_id, started, error=text or "tool error")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = text
        rows = _count_rows(data)
        budget.rows += rows
        return self._finish(role, tool, server, args, invoice_id, started, data=data, size=len(text), rows=rows)

    def _finish(self, role, tool, server, args, invoice_id, started, *, data=None, error=None,
                refused=False, reason=None, size=0, rows=0) -> dict:
        latency_ms = round((time.perf_counter() - started) * 1000)
        self.audit.write(invoice_id=invoice_id, role=role, server=server, tool=tool, args=args,
                         result_size=size, rows_returned=rows, latency_ms=latency_ms,
                         refused=refused, reason=reason or error, error=bool(error))
        return {"ok": not (refused or error), "data": data, "error": reason or error, "refused": refused,
                "rows_returned": rows, "server": server, "latency_ms": latency_ms}


def _count_rows(data: Any) -> int:
    if not isinstance(data, dict):
        return 0
    if isinstance(data.get("returned"), int):
        return data["returned"]
    return sum(len(v) for v in data.values() if isinstance(v, list))
