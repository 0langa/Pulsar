"""Centralized secret redaction.

Applied before console output, logs, session DB writes, tool results returned
to the model, and exports. Masks both known secret values and generic
credential patterns.
"""

from __future__ import annotations

import re

MASK = "[REDACTED]"

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
    def __init__(self, known_values: list[str] | None = None, enabled: bool = True):
        self.enabled = enabled
        self._values: set[str] = set()
        for value in known_values or []:
            self.register_value(value)

    def register_value(self, value: str | None) -> None:
        if value and len(value) >= 6:
            self._values.add(value)

    def redact(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        out = text
        for value in self._values:
            if value in out:
                out = out.replace(value, MASK)
        out = _ASSIGNMENT_PATTERN.sub(
            lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{MASK}{m.group(4)}", out
        )
        out = _BEARER_PATTERN.sub(lambda m: f"{m.group(1)}{MASK}", out)
        for pattern in _VALUE_PATTERNS:
            out = pattern.sub(MASK, out)
        return out
