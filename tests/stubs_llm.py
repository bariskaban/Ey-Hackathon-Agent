"""A stub model for evaluation tests."""

from types import SimpleNamespace

from agent.schemas import Invoice


class DocumentAwareLLM:
    """Extracts the right ground-truth invoice for whichever document it is shown."""

    def __init__(self, ground_truth: list[dict], model: str = "stub-model"):
        self.model = model
        self.calls = 0
        self._by_number = {
            g["expected_invoice"]["invoice_number"]: Invoice.model_validate(g["expected_invoice"]).model_dump_json()
            for g in ground_truth}

    async def chat(self, messages, **kwargs):
        self.calls += 1
        text = messages[-1]["content"]
        for number, invoice_json in self._by_number.items():
            if number in text:
                return SimpleNamespace(message=SimpleNamespace(content=invoice_json, tool_calls=None))
        raise AssertionError("no known invoice number in the document text")
