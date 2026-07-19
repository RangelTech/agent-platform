"""LLM provider layer.

Everything the graph needs from a model goes through `stream_completion`, an
async generator of text deltas. Two implementations:

- litellm: the real path — provider-agnostic via litellm.acompletion(stream=True)
- stub: deterministic scripted responses for tests. The stub is part of the
  product's test seam, not test code: suites drive the whole HTTP surface with
  it and never spend a token.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class ModelConfig:
    """Resolved model configuration for one completion call."""

    provider: str  # litellm provider prefix, e.g. "gemini", "openai", "stub"
    model: str  # model name, e.g. "gemini-2.5-flash"
    api_key: str | None = None
    api_base: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    extra: dict = field(default_factory=dict)

    @property
    def litellm_model(self) -> str:
        return f"{self.provider}/{self.model}" if self.provider else self.model


class StubScript:
    """Deterministic responses keyed by substring of the last user message.

    Falls back to `default`. Exposed via kernel settings for the test suites.
    """

    def __init__(self, rules: list[tuple[str, str]] | None = None, default: str = "ok"):
        self.rules = rules or []
        self.default = default

    def reply_for(self, prompt: str) -> str:
        for needle, reply in self.rules:
            if needle.lower() in prompt.lower():
                return reply
        return self.default


# Mutable so tests can swap scripts at runtime through the /stub endpoint.
stub_script = StubScript(
    rules=[
        ("erro proposital", "__RAISE__"),
    ],
    default="Resposta simulada do stub.",
)


async def _stream_stub(messages: list[dict]) -> AsyncIterator[str]:
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
    )
    if isinstance(last_user, list):  # multimodal content blocks
        last_user = " ".join(
            b.get("text", "") for b in last_user if isinstance(b, dict)
        )
    reply = stub_script.reply_for(last_user)
    if reply == "__RAISE__":
        raise RuntimeError("stub provider error (scripted)")
    # Stream word by word so SSE behaviour is genuinely exercised.
    words = reply.split(" ")
    for i, word in enumerate(words):
        yield word if i == 0 else " " + word


async def _stream_litellm(config: ModelConfig, messages: list[dict]) -> AsyncIterator[str]:
    import litellm

    response = await litellm.acompletion(
        model=config.litellm_model,
        messages=messages,
        api_key=config.api_key,
        api_base=config.api_base,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        stream=True,
        **config.extra,
    )
    async for chunk in response:
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


def stream_completion(config: ModelConfig, messages: list[dict]) -> AsyncIterator[str]:
    if config.provider == "stub":
        return _stream_stub(messages)
    return _stream_litellm(config, messages)
