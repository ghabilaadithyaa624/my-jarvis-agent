"""Security risk levels and permission decision structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class RiskLevel(str, Enum):
    SAFE = "SAFE"              # Read-only queries, time, system metrics, notes read
    MODERATE = "MODERATE"      # Open allowlisted app, create new file, organize folder
    SENSITIVE = "SENSITIVE"    # Delete/overwrite file, kill process, install package, send message
    CRITICAL = "CRITICAL"      # Admin commands, security settings, financial actions
    BLOCKED = "BLOCKED"        # Destructive OS commands, format, firewall disable, path escape


class PermissionDecision(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"
    DENY = "DENY"


@dataclass
class PermissionCheckResult:
    decision: PermissionDecision
    risk_level: RiskLevel
    reason: str
    target_summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfirmationRequest:
    tool_name: str
    risk_level: RiskLevel
    reason: str
    target_summary: str
    arguments: Dict[str, Any]
    timeout_sec: float = 60.0
