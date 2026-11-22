"""UI-independent confirmed mutation execution and sanitized local audit events."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Literal
from uuid import uuid4

from datasifter.datalens.adapters.base import DirectoryAdapter
from datasifter.datalens.errors import ConfirmationRequiredError, DataLensError
from datasifter.datalens.models import (
    DirectoryBackend,
    MutationResult,
    PreparedMutation,
)

AuditOutcome = Literal["declined", "attempted", "applied", "failed_or_unknown"]
Operation = Literal["create", "update", "delete", "move"]


class AuditLogError(DataLensError):
    """The audit event could not be persisted."""


class LocalAuditLog:
    """Append metadata-only JSON lines; no directory values or exception text."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record(
        self, event_id: str, operation: Operation, backend: DirectoryBackend, outcome: AuditOutcome
    ) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_id": event_id,
            "operation": operation,
            "backend": backend.value,
            "outcome": outcome,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            raise AuditLogError("Unable to persist the Data Lens audit event.") from error


class DataLensMutationService:
    """Apply exactly the reviewed plan, with confirmation and a fresh preview."""

    def __init__(self, adapter: DirectoryAdapter, audit: LocalAuditLog) -> None:
        self.adapter = adapter
        self.audit = audit

    def decline(self, prepared: PreparedMutation) -> None:
        if not prepared.ready or prepared.plan is None:
            raise DataLensError("A valid mutation preview is required.")
        self.audit.record(uuid4().hex, prepared.plan.kind, self.adapter.backend, "declined")

    def apply(
        self,
        prepared: PreparedMutation,
        *,
        confirmed: bool = False,
        cancelled: Event | None = None,
    ) -> MutationResult:
        plan, preview = prepared.plan, prepared.preview
        if not prepared.ready or plan is None or preview is None:
            raise DataLensError("A valid mutation preview is required.")
        if not confirmed:
            raise ConfirmationRequiredError(preview)
        if preview.backend != self.adapter.backend:
            raise DataLensError("The reviewed preview belongs to a different directory backend.")

        event_id = uuid4().hex
        self.audit.record(event_id, plan.kind, self.adapter.backend, "attempted")
        try:
            result = self.adapter.execute(
                plan,
                dry_run=False,
                confirmed=True,
                cancelled=cancelled,
                expected_preview=preview,
            )
        except Exception:
            try:
                self.audit.record(event_id, plan.kind, self.adapter.backend, "failed_or_unknown")
            except AuditLogError as error:
                raise AuditLogError(
                    "The operation failed or its outcome is unknown, and audit recording "
                    "failed. Verify the directory before retrying."
                ) from error
            raise
        try:
            self.audit.record(event_id, plan.kind, self.adapter.backend, "applied")
        except AuditLogError as error:
            raise AuditLogError(
                "The directory change was applied, but its audit outcome could not be "
                "recorded. Verify the directory before retrying."
            ) from error
        return result
