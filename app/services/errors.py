"""Domain errors with safe user-facing messages."""


class AccessPilotError(RuntimeError):
    """Base class for expected AccessPilot failures."""


class SafetyViolation(AccessPilotError):
    """Raised when an operation crosses an explicit safety boundary."""


class ToolUnavailable(AccessPilotError):
    """Raised when an optional local tool has not been installed."""


class ApprovalRequired(SafetyViolation):
    """Raised when a remote-changing action lacks matching approval."""
