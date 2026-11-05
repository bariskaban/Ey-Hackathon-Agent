"""MCP server: vendor and bank checks (IBAN, bank details, sanctions, employee overlap)."""

from mcp.server.mcpserver import MCPServer

from servers.common.runner import register_tools, run_server
from servers.vendor_checks import tools

server = MCPServer(
    "vendor_checks",
    instructions="IBAN validation, comparison with the vendor master, sanctions screening and vendor/employee "
                 "overlap. IBANs are masked and employee details are never returned.",
)

register_tools(server, [tools.validate_iban, tools.compare_bank_details, tools.screen_sanctions,
                        tools.vendor_employee_overlap])

if __name__ == "__main__":
    run_server(server, default_port=8104)
