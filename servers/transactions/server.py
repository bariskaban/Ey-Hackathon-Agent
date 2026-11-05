"""MCP server: the company's transaction records (vendors, invoices seen, payments, POs)."""

from mcp.server.mcpserver import MCPServer

from servers.common.runner import register_tools, run_server
from servers.transactions import tools

server = MCPServer(
    "transactions",
    instructions="Read-only access to the company's vendors, invoices already seen, payments and "
                 "purchase orders. IBANs are masked and results are capped at 20 rows.",
)

register_tools(server, [tools.get_vendor, tools.lookup_invoice_number, tools.find_transactions,
                        tools.get_purchase_order, tools.vendor_payment_summary])

if __name__ == "__main__":
    run_server(server, default_port=8102)
