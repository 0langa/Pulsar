"""Approval presets and the approval decision pipeline.

Presets (see SECURITY.md):
- paranoid: approve every terminal command (even read-only) and every mutating
  action. Only workspace file *reads* are auto-approved.
- review (default): auto-approve low-risk local reads and SAFE terminal
  commands; ask for file writes, patches, execute_code, memory writes, and any
  mutating/risky terminal command.
- trusted-local: same low-risk auto set as review, PLUS it honors explicit,
  per-capability autonomy grants from config (`security.autonomy.*`) and the
  exact-match `security.command_allowlist`. Grants are opt-in and never the
  default. They only reduce prompts for the specific capability granted.

Non-overridable guarantees (defense in depth):
- Hardline-BLOCKED requests are refused here regardless of preset, approver,
  allowlist, or autonomy grant. Tools also refuse BLOCKED before ever building
  a request.
- Autonomy grants take effect only under the `trusted-local` preset. Under
  `review`/`paranoid` they are ignored (stricter always wins).
- No grant or allowlist can auto-approve a mutating/risky terminal command
  unless the exact command string is in the user's command_allowlist; even then
  BLOCKED commands are never allowed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pulsar_agent.security.command_risk import RiskTier

PRESETS = ("paranoid", "review", "trusted-local")

# Action kinds used by tools.
KIND_READ = "read"
KIND_WRITE = "write"
KIND_TERMINAL = "terminal"
KIND_EXECUTE_CODE = "execute_code"
KIND_MEMORY_WRITE = "memory_write"
KIND_NETWORK = "network"
KIND_MCP = "mcp"

# Autonomy grant keys (config: security.autonomy.*). All default False.
AUTONOMY_KEYS = (
    "allow_writes", "allow_execute_code", "allow_memory_writes", "allow_mcp",
)
_KIND_TO_GRANT = {
    KIND_WRITE: "allow_writes",
    KIND_EXECUTE_CODE: "allow_execute_code",
    KIND_MEMORY_WRITE: "allow_memory_writes",
    KIND_MCP: "allow_mcp",
}


class ApprovalDenied(PermissionError):
    pass


@dataclass
class ApprovalRequest:
    kind: str
    description: str
    risk: RiskTier = RiskTier.APPROVAL
    detail: str = ""
    cwd: str = ""
    will_checkpoint: bool = False


@dataclass
class ApprovalManager:
    preset: str
    approver: Callable[[ApprovalRequest], bool] | None = None
    command_allowlist: list[str] = field(default_factory=list)
    autonomy: dict = field(default_factory=dict)
    audit_log: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.preset not in PRESETS:
            raise ValueError(f"unknown approval preset {self.preset!r}")

    def _grant(self, key: str) -> bool:
        # Grants only apply under trusted-local; stricter presets ignore them.
        if self.preset != "trusted-local":
            return False
        return bool(self.autonomy.get(key, False))

    def _auto_allows(self, request: ApprovalRequest) -> bool:
        if request.risk is RiskTier.BLOCKED:
            return False

        # Workspace file reads are low-risk (path policy already blocks
        # credential files) and auto-approved in every preset.
        if request.kind == KIND_READ:
            return True

        if request.kind == KIND_TERMINAL:
            if request.risk is RiskTier.SAFE:
                # Low-risk local commands: auto in review/trusted-local only.
                return self.preset in ("review", "trusted-local")
            # Mutating/risky terminal: only an exact command-allowlist match
            # can skip the prompt, and never under paranoid.
            if (
                self.preset != "paranoid"
                and request.description.strip() in self.command_allowlist
            ):
                return True
            return False

        if request.kind == KIND_NETWORK:
            # Read-only web retrieval: auto in review/trusted-local, prompted
            # under paranoid (a fetched URL is also an exfiltration channel).
            return self.preset in ("review", "trusted-local")

        grant_key = _KIND_TO_GRANT.get(request.kind)
        if grant_key is not None:
            return self._grant(grant_key)

        return False

    def check(self, request: ApprovalRequest) -> None:
        """Raise ApprovalDenied unless the action is auto-allowed or approved."""
        if request.risk is RiskTier.BLOCKED:
            # Defense in depth: tools must reject hardline commands before
            # ever building an approval request. This is the backstop.
            self.audit_log.append(f"BLOCKED {request.kind}: {request.description}")
            raise ApprovalDenied(
                f"hardline blocked: {request.detail or request.description}"
            )
        if self._auto_allows(request):
            self.audit_log.append(f"AUTO {request.kind}: {request.description}")
            return
        if self.approver is None:
            self.audit_log.append(
                f"DENIED(no-approver) {request.kind}: {request.description}"
            )
            raise ApprovalDenied(
                f"{request.kind} requires approval "
                f"({request.detail or 'no approver available'})"
            )
        if self.approver(request):
            self.audit_log.append(f"APPROVED {request.kind}: {request.description}")
            return
        self.audit_log.append(f"DENIED {request.kind}: {request.description}")
        raise ApprovalDenied(f"user denied {request.kind}: {request.description}")


def autonomy_from_config(config: dict) -> dict:
    section = (config.get("security", {}) or {}).get("autonomy", {}) or {}
    return {key: bool(section.get(key, False)) for key in AUTONOMY_KEYS}


def build_approval_manager(
    config: dict,
    approver: Callable[[ApprovalRequest], bool] | None,
) -> ApprovalManager:
    security = config.get("security", {}) or {}
    return ApprovalManager(
        preset=config.get("approval_preset", "review"),
        approver=approver,
        command_allowlist=list(security.get("command_allowlist") or []),
        autonomy=autonomy_from_config(config),
    )
