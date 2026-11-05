"""MCP server: review cases, the only write access. Append-only; agents cannot approve or close a case."""

from mcp.server.mcpserver import MCPServer

from servers.common.runner import register_tools, run_server
from servers.review_cases import tools

server = MCPServer(
    "review_cases",
    instructions="Open review cases, add notes and export evidence packs. Append-only: nothing can be "
                 "changed or deleted, and only a person can approve or close a case.",
)

register_tools(server, [tools.create_review_case, tools.add_case_note, tools.export_evidence_pack])

if __name__ == "__main__":
    run_server(server, default_port=8106)
