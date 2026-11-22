"""Data Lens engine: typed, backend-aware directory operations.

This package has no user-interface dependencies. It is the layer that the
natural-language interface, and later an API or MCP server, will call:

* :mod:`datasifter.datalens.models` - validated request and result models.
* :mod:`datasifter.datalens.adapters` - Standard LDAP and Active Directory backends.
* :mod:`datasifter.datalens.aliases` - everyday terms for attributes, per backend.
* :mod:`datasifter.datalens.resolver` - terms and names to attributes, entries and plans,
  or a clarification when something is ambiguous or missing.
* :mod:`datasifter.datalens.intents` - strict natural-language intent schemas and preparation.
* :mod:`datasifter.datalens.service` - confirmed execution and sanitized local audit events.
* :mod:`datasifter.datalens.help` - structured capabilities, aliases, and examples.
* :mod:`datasifter.datalens.dn` - distinguished-name helpers.
* :mod:`datasifter.datalens.errors` - errors raised when a change is refused.

Example::

    service = LDAPService(profile, password)
    adapter = create_adapter(service, DirectoryBackend.STANDARD_LDAP)
    preview = adapter.delete_entry("cn=old,ou=People,dc=example,dc=com")  # dry run
    if preview.preview.can_apply:
        adapter.delete_entry(
            "cn=old,ou=People,dc=example,dc=com", dry_run=False, confirmed=True
        )
"""

from datasifter.datalens.adapters import (
    ActiveDirectoryAdapter,
    DirectoryAdapter,
    StandardLdapAdapter,
    create_adapter,
    detect_backend,
)
from datasifter.datalens.aliases import AliasRegistry, AttributeConcept
from datasifter.datalens.errors import (
    ConfirmationRequiredError,
    DataLensError,
    MutationRejectedError,
    PreviewChangedError,
    ReadOnlyDirectoryError,
)
from datasifter.datalens.help import HelpCatalog, get_help_catalog
from datasifter.datalens.intents import (
    ClarifyIntent,
    CreateProposal,
    DeleteProposal,
    DirectoryIntent,
    MoveProposal,
    PreparedIntent,
    ProposedMutationIntent,
    SearchIntent,
    UpdateProposal,
    prepare_intent,
)
from datasifter.datalens.models import (
    AccountState,
    AccountStatus,
    AttributeChange,
    AttributeResolution,
    ChangeOperation,
    ClarificationOption,
    ClarificationPrompt,
    ClarificationReason,
    CreatePlan,
    DeletePlan,
    DirectoryBackend,
    DirectoryEntry,
    DryRunResult,
    EntryKind,
    IssueCode,
    IssueSeverity,
    MatchOperator,
    MovePlan,
    MutationPlan,
    MutationResult,
    PlanIssue,
    PreparedMutation,
    PreparedSearch,
    RequestedChange,
    SearchCondition,
    SearchResult,
    SearchScope,
    SearchSpec,
    UpdatePlan,
)
from datasifter.datalens.resolver import DirectoryResolver
from datasifter.datalens.service import AuditLogError, DataLensMutationService, LocalAuditLog

__all__ = [
    "AccountState",
    "AccountStatus",
    "ActiveDirectoryAdapter",
    "AliasRegistry",
    "AttributeChange",
    "AttributeConcept",
    "AttributeResolution",
    "AuditLogError",
    "ChangeOperation",
    "ClarificationOption",
    "ClarificationPrompt",
    "ClarificationReason",
    "ClarifyIntent",
    "ConfirmationRequiredError",
    "CreatePlan",
    "CreateProposal",
    "DataLensError",
    "DataLensMutationService",
    "DeletePlan",
    "DeleteProposal",
    "DirectoryAdapter",
    "DirectoryBackend",
    "DirectoryEntry",
    "DirectoryIntent",
    "DirectoryResolver",
    "DryRunResult",
    "EntryKind",
    "HelpCatalog",
    "IssueCode",
    "IssueSeverity",
    "LocalAuditLog",
    "MatchOperator",
    "MovePlan",
    "MoveProposal",
    "MutationPlan",
    "MutationRejectedError",
    "MutationResult",
    "PlanIssue",
    "PreparedIntent",
    "PreparedMutation",
    "PreparedSearch",
    "PreviewChangedError",
    "ProposedMutationIntent",
    "ReadOnlyDirectoryError",
    "RequestedChange",
    "SearchCondition",
    "SearchIntent",
    "SearchResult",
    "SearchScope",
    "SearchSpec",
    "StandardLdapAdapter",
    "UpdatePlan",
    "UpdateProposal",
    "create_adapter",
    "detect_backend",
    "get_help_catalog",
    "prepare_intent",
]
