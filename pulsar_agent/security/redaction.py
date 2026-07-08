"""Centralized secret redaction.

Applied before console output, logs, session DB writes, tool results returned
to the model, and exports. Masks both known secret values and generic
credential patterns.

Known values of 6+ chars are masked anywhere they appear. Shorter known
values (down to `min_length`, default 3) are masked only as standalone
tokens — plain substring replacement would shred ordinary words that happen
to contain them.
"""

from __future__ import annotations

import re

MASK = "[REDACTED]"

# Hard floor for the configurable minimum: 1-2 char "secrets" would mask
# single letters across all output.
ABSOLUTE_MIN_LENGTH = 3
SUBSTRING_MIN_LENGTH = 6

SECRET_NAME_RE = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.IGNORECASE
)

_VALUE_PATTERNS = [
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
]

_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}")

_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)[A-Z0-9_]*)"
    r"(\s*[=:]\s*)(['\"]?)(?!\[REDACTED\])[^\s'\"]{6,}(\3)"
)


class Redactor:
    def __init__(
        self,
        known_values: list[str] | None = None,
        enabled: bool = True,
        min_length: int = ABSOLUTE_MIN_LENGTH,
    ):
        self.enabled = enabled
        self.min_length = max(ABSOLUTE_MIN_LENGTH, int(min_length))
        self._values: set[str] = set()
        self._short_patterns: dict[str, re.Pattern] = {}
        for value in known_values or []:
            self.register_value(value)

    def register_value(self, value: str | None) -> None:
        if not value or len(value) < self.min_length:
            return
        if len(value) >= SUBSTRING_MIN_LENGTH:
            self._values.add(value)
        elif value not in self._short_patterns:
            # Standalone-token match; lookarounds instead of \b so values with
            # non-word edge characters still anchor correctly.
            self._short_patterns[value] = re.compile(
                rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])"
            )

    def redact(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        out = text
        for value in self._values:
            if value in out:
                out = out.replace(value, MASK)
        for pattern in self._short_patterns.values():
            out = pattern.sub(MASK, out)
        out = _ASSIGNMENT_PATTERN.sub(
            lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{MASK}{m.group(4)}", out
        )
        out = _BEARER_PATTERN.sub(lambda m: f"{m.group(1)}{MASK}", out)
        for pattern in _VALUE_PATTERNS:
            out = pattern.sub(MASK, out)
        return out
