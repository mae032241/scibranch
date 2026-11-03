"""Typed acknowledgement that an exact submission was rejected before creation."""
from __future__ import annotations


from ..operation_contract import DiagnosticError, contract_diagnostic


class SubmissionNotCreated(DiagnosticError):
    REASONS = frozenset({"capacity_exhausted", "submission_retired", "local_validation_failed"})

    def __init__(self, submission_sha256: str, reason: str):
        if (reason not in self.REASONS or not isinstance(submission_sha256, str)
                or len(submission_sha256) != 64
                or any(c not in "0123456789abcdef" for c in submission_sha256)):
            raise ValueError("Invalid submission rejection identity")
        self.submission_sha256, self.reason = submission_sha256, reason
        message = "The exact submission was not created: " + reason
        super().__init__(message, details=(contract_diagnostic(
            "execution_capacity_busy" if reason == "capacity_exhausted" else "execution_not_submitted",
            phase="tool_execution", affected_action="tool_call", repairable=True, message=message),))

    def wire(self):
        return {"state": "not_created", "submission_sha256": self.submission_sha256, "reason": self.reason}

    @classmethod
    def from_wire(cls, value, *, expected_sha256):
        if (isinstance(value, dict) and value.get("state") == "not_created"
                and value.get("submission_sha256") == expected_sha256
                and value.get("reason") in cls.REASONS):
            return cls(expected_sha256, value["reason"])
        return None
