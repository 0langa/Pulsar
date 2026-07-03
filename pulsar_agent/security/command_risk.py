"""Command risk classification with a non-overridable hardline blocklist.

Tiers:
- SAFE: read-only inspection and non-mutating test commands.
- APPROVAL: anything that mutates state; needs user approval per preset.
- BLOCKED: hardline catastrophic patterns; refused in every preset and
  never overridable by config, allowlists, or prompts.
"""

from __future__ import annotations

import re
from enum import Enum


class RiskTier(Enum):
    SAFE = "safe"
    APPROVAL = "approval"
    BLOCKED = "blocked"


class HardlineBlocked(PermissionError):
    pass


_HARDLINE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\brm\s+(?:-[a-z]+\s+)*(?:-rf|-fr|-r\s+-f|-f\s+-r|--recursive)\s+(?:--no-preserve-root\s+)?['\"]?/+\*?['\"]?(?=$|[\s;&|')])"),
     "recursive delete of filesystem root"),
    (re.compile(r"(?i)\brm\s+.*--no-preserve-root"), "rm --no-preserve-root"),
    (re.compile(r"(?i)\bmkfs(\.\w+)?\b"), "disk formatting"),
    (re.compile(r"(?i)\bformat\s+[a-z]:"), "disk formatting"),
    (re.compile(r"(?i)\bdd\b[^|;]*\bof=/dev/(sd|hd|nvme|disk|mmcblk)"), "raw write to block device"),
    (re.compile(r"(?i)>\s*/dev/(sd|hd|nvme|disk|mmcblk)"), "raw write to block device"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "fork bomb"),
    (re.compile(r"(?i)\b(del|rd|rmdir)\s+(/[sqf]\s+)+([\"']?)[a-z]:\\?\3(\s|$)"), "recursive delete of drive root"),
    (re.compile(r"(?i)remove-item\s+.*-recurse.*\s([\"']?)[a-z]:\\?\1(\s|$)"), "recursive delete of drive root"),
    (re.compile(r"(?i)\b(curl|wget|iwr|invoke-webrequest)\b.*\|\s*sudo\s+\S*(sh|bash|zsh)\b"), "piping remote script to a privileged shell"),
    (re.compile(r"(?i)\b(curl|wget)\b[^|]*\|\s*\S*(sh|bash|zsh)\b[^#]*\s(/etc|/usr|/boot|/var)\b"), "piping remote script to shell against system paths"),
    (re.compile(r"(?i)\bchmod\s+(-r\s+)?777\s+/\s*($|\*)"), "world-writable filesystem root"),
    (re.compile(r"(?i)cipher\s+/w"), "disk wipe"),
    (re.compile(r"(?i)\bdiskpart\b"), "disk partitioning tool"),
]

_SAFE_PATTERNS = [
    re.compile(r"(?i)^\s*(ls|ll|dir|pwd|cd|whoami|hostname|date|echo|type|cat|head|tail|wc|which|where)\b[^|;&>]*$"),
    re.compile(r"(?i)^\s*(find|rg|grep|fd|tree)\b[^|;&>]*$"),
    re.compile(r"(?i)^\s*git\s+(status|log|diff|show|branch|remote|describe|rev-parse|ls-files|blame|shortlog)\b[^|;&>]*$"),
    re.compile(r"(?i)^\s*(python|python3|py)\s+(--version|-V|-m\s+pytest\b.*)\s*$"),
    re.compile(r"(?i)^\s*(pytest|tox)\b[^|;&>]*$"),
    re.compile(r"(?i)^\s*(pip|pip3|uv)\s+(list|show|freeze|--version)\b[^|;&>]*$"),
    re.compile(r"(?i)^\s*(node|npm|pnpm|yarn|cargo|go|rustc|dotnet|java|ruby)\s+(--version|-v|version)\s*$"),
    re.compile(r"(?i)^\s*(npm|pnpm|yarn)\s+(test|ls|list)\b[^|;&>]*$"),
    re.compile(r"(?i)^\s*cargo\s+(test|check|--list)\b[^|;&>]*$"),
    re.compile(r"(?i)^\s*go\s+(test|vet|version)\b[^|;&>]*$"),
    re.compile(r"(?i)^\s*dotnet\s+test\b[^|;&>]*$"),
]

_SENSITIVE_REFERENCE = re.compile(
    r"(?i)(\.env\b|auth\.json|secrets\.enc|id_rsa|id_ed25519|\.git[/\\]credentials|\.pem\b|\.ssh\b)"
)


def classify_command(command: str) -> tuple[RiskTier, str]:
    """Classify a shell command. Hardline matches always win."""
    stripped = (command or "").strip()
    if not stripped:
        return RiskTier.SAFE, "empty command"
    for pattern, reason in _HARDLINE_PATTERNS:
        if pattern.search(stripped):
            return RiskTier.BLOCKED, reason
    if _SENSITIVE_REFERENCE.search(stripped):
        return RiskTier.APPROVAL, "references sensitive credential paths"
    for pattern in _SAFE_PATTERNS:
        if pattern.match(stripped):
            return RiskTier.SAFE, "read-only or non-mutating test command"
    return RiskTier.APPROVAL, "potentially mutating command"


def assert_not_hardline(command: str) -> None:
    tier, reason = classify_command(command)
    if tier is RiskTier.BLOCKED:
        raise HardlineBlocked(f"hardline blocklist: {reason}")
