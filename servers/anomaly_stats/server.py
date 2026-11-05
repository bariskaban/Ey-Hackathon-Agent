"""MCP server: aggregate statistics (scores and counts, never rows)."""

from mcp.server.mcpserver import MCPServer

from servers.anomaly_stats import tools
from servers.common.runner import register_tools, run_server

server = MCPServer(
    "anomaly_stats",
    instructions="Scores and counts about a vendor's history, never individual transactions. "
                 "Groups with fewer than 5 records are suppressed.",
)

register_tools(server, [tools.vendor_amount_profile, tools.split_invoice_check, tools.date_flags])

if __name__ == "__main__":
    run_server(server, default_port=8105)
