"""Errors raised by the Data Lens engine.

Each mutation error carries the preview that led to it, so a caller can show the
person what was checked without repeating the lookups.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datasifter.datalens.models import DryRunResult


class DataLensError(Exception):
    """Base class for engine errors that are not plain validation failures."""


class ReadOnlyDirectoryError(DataLensError):
    """A change was requested on a read-only profile or adapter."""


class MutationRejectedError(DataLensError):
    """The plan has blocking issues, so nothing was sent to the directory."""

    def __init__(self, preview: DryRunResult) -> None:
        self.preview = preview
        problems = "; ".join(
            issue.message for issue in preview.issues if issue.severity.value == "error"
        )
        super().__init__(f"The change was not applied: {problems}")


class ConfirmationRequiredError(DataLensError):
    """The plan is valid but the caller did not confirm it, so nothing was changed."""

    def __init__(self, preview: DryRunResult) -> None:
        self.preview = preview
        super().__init__(f"Confirmation is required before applying: {preview.summary}")


class PreviewChangedError(DataLensError):
    """The directory no longer matches the preview the user approved."""

    def __init__(self) -> None:
        super().__init__("The directory changed since review. Generate and review a new preview.")
