"""``AIUserJudge`` -- let an LLM decide pass/fail from an arbitrary TUI screen.

The chat-suite tests assert on a live, non-deterministic TUI. Rather than brittle
substring matching against a screen whose layout can shift, we hand the captured
screen plus a yes/no question to Claude and require a strict-JSON verdict. This keeps
the tests robust to cosmetic rendering changes while still being objective.

Uses the ``anthropic`` package when importable, else falls back to a raw ``httpx`` POST
to the Messages API. On any API/parse error it raises -- it never silently passes.

The verdict is obtained via Anthropic's native **structured outputs** (GA): the request
carries an ``output_config.format`` json_schema so the model's response is constrained by
grammar to exactly ``{"passed": bool, "reason": str}``. This is guaranteed schema-valid,
so there is no prompt-engineered "please reply with JSON" and no retry loop. The
``anthropic-version: 2023-06-01`` header is still the current stable API version (the SDK
sends it automatically; the raw-HTTP path sets it explicitly). No beta header is needed
for structured outputs now that they are GA.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

_SYSTEM = (
    "You are a strict QA judge for a terminal UI (TUI) end-to-end test. "
    "You are given a snapshot of the terminal screen and a yes/no question about it. "
    "Judge only what is visible on the screen. Set 'passed' to whether the answer to the "
    "question is yes, and 'reason' to a short explanation."
)

# JSON schema for the verdict, enforced by Anthropic structured outputs (constrained
# decoding). additionalProperties:false keeps the model to exactly these two fields.
_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["passed", "reason"],
    "additionalProperties": False,
}
_OUTPUT_CONFIG: dict[str, Any] = {"format": {"type": "json_schema", "schema": _VERDICT_SCHEMA}}

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"


@dataclass(frozen=True)
class Verdict:
    """The judge's decision for one screen+question pair."""

    passed: bool
    reason: str


class JudgeError(RuntimeError):
    """Raised when the judge cannot obtain or parse a verdict."""


class AIUserJudge:
    """Evaluate TUI screens against yes/no questions via the Anthropic API."""

    def __init__(
        self, model: str = "claude-sonnet-5", api_key_env: str = "ANTHROPIC_API_KEY"
    ) -> None:
        self.model = model
        self.api_key_env = api_key_env

    def _api_key(self) -> str:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise JudgeError(f"{self.api_key_env} is not set; cannot reach the Anthropic API")
        return key

    def evaluate(self, screen: str, question: str) -> Verdict:
        """Return a pass/fail Verdict for ``question`` about ``screen``.

        Raises:
            JudgeError: On a missing API key, an API failure, or an unparseable reply.
        """
        prompt = f"SCREEN:\n```\n{screen}\n```\n\nQUESTION: {question}"
        raw = self._complete(prompt)
        return self._parse(raw)

    def _complete(self, prompt: str) -> str:
        """Call the Messages API and return the assistant's text content."""
        api_key = self._api_key()
        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError:
            return self._complete_httpx(api_key, prompt)

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=self.model,
            max_tokens=256,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config=_OUTPUT_CONFIG,  # native structured outputs -> schema-valid JSON
        )
        parts = [block.text for block in message.content if getattr(block, "type", None) == "text"]
        text = "".join(parts).strip()
        if not text:
            raise JudgeError("Anthropic returned an empty message")
        return text

    def _complete_httpx(self, api_key: str, prompt: str) -> str:
        """Fallback path: raw httpx POST to the Messages API."""
        import httpx

        payload = {
            "model": self.model,
            "max_tokens": 256,
            "system": _SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": _OUTPUT_CONFIG,  # native structured outputs (GA, no beta header)
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        try:
            response = httpx.post(_ANTHROPIC_URL, json=payload, headers=headers, timeout=60.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise JudgeError(f"Anthropic API request failed: {exc}") from exc

        body = response.json()
        blocks = body.get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        if not text:
            raise JudgeError(f"Anthropic returned no text content: {body!r}")
        return text

    @staticmethod
    def _parse(raw: str) -> Verdict:
        """Extract the first JSON object from ``raw`` and coerce it to a Verdict."""
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise JudgeError(f"no JSON object in judge reply:\n{raw}")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise JudgeError(f"could not parse judge JSON: {exc}\nreply:\n{raw}") from exc
        if "passed" not in data:
            raise JudgeError(f"judge JSON missing 'passed': {data!r}")
        return Verdict(passed=bool(data["passed"]), reason=str(data.get("reason", "")))
