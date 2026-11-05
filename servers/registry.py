"""The MCP servers of this project. A server is started only if its module exists yet."""

import importlib.util
from dataclasses import dataclass


@dataclass(frozen=True)
class ServerInfo:
    name: str
    module: str  # run with: python -m <module>
    port: int  # only used when started over HTTP (scripts/start_servers.py)
    description: str

    def is_built(self) -> bool:
        try:
            return importlib.util.find_spec(self.module) is not None
        except ModuleNotFoundError:
            return False


SERVERS: dict[str, ServerInfo] = {
    "documents": ServerInfo("documents", "servers.documents.server", 8101,
                            "Invoice files to Markdown (MarkItDown, OCR fallback)"),
    "transactions": ServerInfo("transactions", "servers.transactions.server", 8102,
                               "Vendors, invoices seen, payments, purchase orders"),
    "tax_rules": ServerInfo("tax_rules", "servers.tax_rules.server", 8103,
                            "Dutch VAT rules"),
    "vendor_checks": ServerInfo("vendor_checks", "servers.vendor_checks.server", 8104,
                                "IBAN, bank details, sanctions, employee overlap"),
    "anomaly_stats": ServerInfo("anomaly_stats", "servers.anomaly_stats.server", 8105,
                                "Aggregate scores, never rows"),
    "review_cases": ServerInfo("review_cases", "servers.review_cases.server", 8106,
                               "The only write access: review cases and evidence packs"),
}


def built_servers() -> dict[str, ServerInfo]:
    return {name: info for name, info in SERVERS.items() if info.is_built()}
