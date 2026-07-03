"""Approval presets and the approval decision pipeline.

Presets (docs/SAFETY_AND_SCOPE_LOCK.md):
- paranoid: approve every terminal command and every write.
- review: auto-approve reads; ask for writes and risky commands.
- trusted-local: auto-approve low-risk local operations; still ask for
  destructive commands. Hardline blocks are enforced before approvals ever
  run and cannot be bypassed by any preset or allowlist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from pulsar_agent.security.command_risk import RiskTier

PRESETS = ("paranoid", "review", "trusted-local")

# Action kinds used by tools.
KIND_READ = "read"
KIND_WRITE = "write"
KIND_TERMINAL = "terminal"
KIND_EXECUTE_CODE = "execute_code"
KIND_MEMORY_WRITE = "memory_write"


class ApprovalDenied(PermissionError):
    pass


@dataclass
class ApprovalRequest:
    kind: str
    description: str
    risk: RiskTier = RiskTier.APPROVAL
    detail: str = ""


@dataclass
class ApprovalManager:
    preset: str
    approver: Callable[[ApprovalRequest], bool] | None = None
    command_allowlist: list[str] = field(default_factory=list)
    audit_log: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.preset not in PRESETS:
            raise ValueError(f"unknown approval preset {self.preset!r}")

    def _auto_allows(self, request: ApprovalRequest) -> bool:
        if request.kind == KIND_READ:
            return True
        if request.kind == KIND_TERMINAL and request.risk is RiskTier.SAFE:
            if self.preset in ("review", "trusted-local"):
                return True
        if self.preset == "trusted-local":
            if request.kind in (KIND_WRITE, KIND_EXECUTE_CODE, KIND_MEMORY_WRITE):
                return True
        if (
            request.kind == KIND_TERMINAL
            and request.risk is not RiskTier.BLOCKED
            and request.description.strip() in self.command_allowlist
        ):
            # Allowlist can skip the prompt for previously approved commands,
            # but never applies to hardline-blocked commands.
            return self.preset != "paranoid"
        return False

    def check(self, request: ApprovalRequest) -> None:
        """Raise ApprovalDenied unless the action is auto-allowed or approved."""
        if request.risk is RiskTier.BLOCKED:
            # Defense in depth: tools must reject hardline commands before
            # ever building an approval request.
            self.audit_log.append(f"BLOCKED {request.kind}: {request.description}")
            raise ApprovalDenied(f"hardline blocked: {request.detail or request.description}")
        if self._auto_allows(request):
            self.audit_log.append(f"AUTO {request.kind}: {request.description}")
            return
        if self.approver is None:
            self.audit_log.append(f"DENIED(no-approver) {request.kind}: {request.description}")
            raise ApprovalDenied(
                f"{request.kind} requires approval ({request.detail or 'no approver available'})"
            )
        if self.approver(request):
            self.audit_log.append(f"APPROVED {request.kind}: {request.description}")
            return
        self.audit_log.append(f"DENIED {request.kind}: {request.description}")
        raise ApprovalDenied(f"user denied {request.kind}: {request.description}")
