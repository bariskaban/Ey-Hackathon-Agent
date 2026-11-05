"""MCP server: Dutch VAT rules (offline, fixed rules only)."""

from mcp.server.mcpserver import MCPServer

from servers.common.runner import register_tools, run_server
from servers.tax_rules import policy, tools

server = MCPServer(
    "tax_rules",
    instructions="Dutch VAT rules: VAT number validation, rates with validity dates, domestic vs reverse "
                 "charge, mandatory invoice fields and invoice arithmetic. Offline, no judgement.",
)

register_tools(server, [tools.validate_vat_number, tools.get_vat_rate, tools.expected_vat_treatment,
                        tools.check_invoice_requirements, tools.validate_invoice_math])

# Resources and a prompt: read-only context a host can attach, not actions a model can call.
server.resource("policy://verification-rules", name="verification-rules", mime_type="text/markdown",
                description="The rules every invoice is checked against, generated from the checks' own settings")(
    policy.verification_rules)
server.resource("schema://invoice", name="invoice-schema", mime_type="application/json",
                description="The JSON schema of an extracted invoice")(policy.invoice_schema)
server.prompt(name="review_findings", description="Explain a verification result to a finance reviewer")(
    policy.review_findings)

if __name__ == "__main__":
    run_server(server, default_port=8103)
