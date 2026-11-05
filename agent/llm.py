"""Thin wrapper around Ollama: fixed local-model settings in one place.

Settings that matter for repeatable results (idea.md section 3):
  * num_ctx is set explicitly, because Ollama's default silently cuts off long prompts and tools
  * temperature 0 and a fixed seed
  * `format` takes a JSON schema, so output is constrained and then validated with Pydantic

Environment: INVOICE_MODEL (default qwen3:8b), OLLAMA_HOST (default http://127.0.0.1:11434),
INVOICE_NUM_CTX (default 12288). Ollama may run in WSL: Windows reaches it on localhost.
"""

import os
from dataclasses import dataclass, field
from typing import Any

import ollama

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_NUM_CTX = 12288
SEED = 42


@dataclass
class LLM:
    model: str = field(default_factory=lambda: os.environ.get("INVOICE_MODEL", DEFAULT_MODEL))
    host: str | None = field(default_factory=lambda: os.environ.get("OLLAMA_HOST"))
    num_ctx: int = field(default_factory=lambda: int(os.environ.get("INVOICE_NUM_CTX", DEFAULT_NUM_CTX)))
    temperature: float = 0.0
    keep_alive: str = "15m"

    def __post_init__(self):
        self._client = ollama.AsyncClient(host=self.host)

    async def chat(self, messages: list[dict], *, tools: list[dict] | None = None,
                   format: dict | str | None = None, think: bool = False) -> Any:
        """One chat call. `format` is a JSON schema (dict) to constrain the answer."""
        return await self._client.chat(
            model=self.model,
            messages=messages,
            tools=tools,
            format=format,
            think=think,
            options={"temperature": self.temperature, "num_ctx": self.num_ctx, "seed": SEED},
            keep_alive=self.keep_alive,
        )

    async def available_models(self) -> list[str]:
        return [m.model for m in (await self._client.list()).models]
