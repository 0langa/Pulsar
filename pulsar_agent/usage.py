"""Per-session token and cost accounting.

Accumulates provider-reported usage across every request the process makes
(turn iterations and subagent calls share one tracker). Cost is computed only
from user-configured prices (`pricing` in config.yaml) — never from a
hardcoded price table that would go stale; without configured prices the
tracker reports tokens only.
"""

from __future__ import annotations

from dataclasses import dataclass


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


@dataclass
class UsageTracker:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    turn_input_tokens: int = 0
    turn_output_tokens: int = 0

    def record(self, usage: dict) -> None:
        """Fold one provider response's usage block into the counters.
        Understands both Anthropic (`input_tokens`/`output_tokens`) and
        OpenAI-style (`prompt_tokens`/`completion_tokens`) field names."""
        if not usage:
            return
        input_tokens = _as_int(
            usage.get("input_tokens") or usage.get("prompt_tokens")
        )
        output_tokens = _as_int(
            usage.get("output_tokens") or usage.get("completion_tokens")
        )
        self.requests += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.turn_input_tokens += input_tokens
        self.turn_output_tokens += output_tokens
        self.cache_read_tokens += _as_int(usage.get("cache_read_input_tokens"))
        self.cache_creation_tokens += _as_int(usage.get("cache_creation_input_tokens"))

    def start_turn(self) -> None:
        self.turn_input_tokens = 0
        self.turn_output_tokens = 0

    def cost(self, pricing: dict | None) -> float | None:
        """USD cost from user-configured per-million-token prices, or None
        when no price is configured (local/free models)."""
        pricing = pricing or {}
        input_per_mtok = float(pricing.get("input_per_mtok") or 0.0)
        output_per_mtok = float(pricing.get("output_per_mtok") or 0.0)
        if input_per_mtok <= 0 and output_per_mtok <= 0:
            return None
        return (
            self.input_tokens / 1_000_000 * input_per_mtok
            + self.output_tokens / 1_000_000 * output_per_mtok
        )

    def summary(self, pricing: dict | None = None) -> str:
        lines = [
            f"requests: {self.requests}",
            f"tokens: {self.input_tokens} in / {self.output_tokens} out "
            f"(last turn: {self.turn_input_tokens} in / {self.turn_output_tokens} out)",
        ]
        if self.cache_read_tokens or self.cache_creation_tokens:
            lines.append(
                f"cache: {self.cache_read_tokens} read / "
                f"{self.cache_creation_tokens} created"
            )
        cost = self.cost(pricing)
        if cost is not None:
            lines.append(f"cost: ${cost:.4f} (from configured pricing)")
        else:
            lines.append(
                "cost: not configured (set pricing.input_per_mtok / "
                "pricing.output_per_mtok in config.yaml)"
            )
        return "\n".join(lines)

    def status_fragment(self) -> str:
        """Compact form for status bars: total in/out tokens."""
        return f"{self.input_tokens}/{self.output_tokens} tok"
