"""Agent roles and what each may touch (least privilege).

`servers` is the set of MCP servers a role may call at all (including fixed checks run by
code). `llm_tools` is the much smaller list the language model itself is shown.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    name: str
    servers: frozenset[str]
    llm_tools: tuple[str, ...]


ROLES: dict[str, Role] = {
    "extractor": Role(
        "extractor",
        frozenset({"documents"}),
        ("convert_document", "get_document_metadata"),
    ),
    "verifier": Role(
        "verifier",
        frozenset({"documents", "transactions", "tax_rules", "vendor_checks", "anomaly_stats"}),
        ("get_vendor", "lookup_invoice_number", "find_transactions", "get_purchase_order",
         "vendor_payment_summary", "convert_document"),
    ),
    "reporter": Role(
        "reporter",
        frozenset({"review_cases"}),
        ("create_review_case", "add_case_note", "export_evidence_pack"),
    ),
}
