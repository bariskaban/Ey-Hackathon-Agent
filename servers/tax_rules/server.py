"""MCP server: Dutch VAT rules (offline, fixed rules only)."""

from mcp.server.mcpserver import MCPServer

from servers.common.runner import register_tools, run_server
from servers.tax_rules import tools

server = MCPServer(
    "tax_rules",
    instructions="Dutch VAT rules: VAT number validation, rates with validity dates, domestic vs reverse "
                 "charge, mandatory invoice fields and invoice arithmetic. Offline, no judgement.",
)

register_tools(server, [tools.validate_vat_number, tools.get_vat_rate, tools.expected_vat_treatment,
                        tools.check_invoice_requirements, tools.validate_invoice_math])

if __name__ == "__main__":
    run_server(server, default_port=8103)
