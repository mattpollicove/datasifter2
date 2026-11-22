"""Structured Data Lens help for desktop, Python, and future API consumers."""

from __future__ import annotations

from typing import Literal

from datasifter.datalens.aliases import AliasRegistry, AttributeConcept
from datasifter.datalens.models import DataLensModel, DirectoryBackend


class IntentHelp(DataLensModel):
    intent: Literal["search", "proposed_mutation", "clarify"]
    operation: Literal["search", "create", "update", "delete", "move", "clarify"]
    description: str
    example: str


class BackendHelp(DataLensModel):
    backend: DirectoryBackend
    differences: tuple[str, ...]


class DirectoryInformationHelp(DataLensModel):
    key: Literal["current_target", "count_people", "monitor"]
    example: str
    description: str


class HelpCatalog(DataLensModel):
    version: Literal[1] = 1
    intents: tuple[IntentHelp, ...]
    directory_information: tuple[DirectoryInformationHelp, ...]
    aliases: tuple[AttributeConcept, ...]
    backends: tuple[BackendHelp, ...]
    safety_constraints: tuple[str, ...]


def get_help_catalog(registry: AliasRegistry | None = None) -> HelpCatalog:
    """Describe the effective alias registry and supported Data Lens operations."""
    registry = registry if registry is not None else AliasRegistry()
    return HelpCatalog(
        intents=(
            IntentHelp(intent="search", operation="search", description="Search directory entries.",
                       example="Find users with the job title Engineer."),
            IntentHelp(intent="proposed_mutation", operation="create",
                       description="Propose creating an entry.",
                       example="Create a user named Alex in the People container with last name Smith."),
            IntentHelp(intent="proposed_mutation", operation="update",
                       description="Propose changing an entry.",
                       example="Set Alex Smith's job title to Engineer."),
            IntentHelp(intent="proposed_mutation", operation="delete",
                       description="Propose deleting an entry.",
                       example="Delete the user Alex Smith."),
            IntentHelp(intent="proposed_mutation", operation="move",
                       description="Propose moving or renaming an entry.",
                       example="Move Alex Smith to the Staff container."),
            IntentHelp(intent="clarify", operation="clarify",
                       description="Ask for missing or ambiguous information.",
                       example="Create a user."),
        ),
        directory_information=(
            DirectoryInformationHelp(
                key="current_target", example="What directory am I connected to?",
                description="Shows the connected profile and its starting location without a model.",
            ),
            DirectoryInformationHelp(
                key="count_people", example="How many people in the directory?",
                description=(
                    "Choose the profile's starting location or a location selected in the tree. "
                    "Counts visible person accounts in that subtree; permissions can hide entries. "
                    "The count uses the backend's person filter and requires paged LDAP results."
                ),
            ),
            DirectoryInformationHelp(
                key="monitor", example="Show directory stats",
                description=(
                    "Reads numeric statistics under cn=monitor where supported and accessible. "
                    "Choose current access, a saved profile for the same endpoint, or one-time "
                    "credentials. Other monitoring APIs are not supported. Statistics are "
                    "limited to the first 200 monitor entries."
                ),
            ),
        ),
        aliases=registry.concepts(),
        backends=(
            BackendHelp(
                backend=DirectoryBackend.STANDARD_LDAP,
                differences=(
                    "User and group object classes and account-state attributes depend on the server schema.",
                    "Username commonly maps to uid; group membership commonly uses member.",
                ),
            ),
            BackendHelp(
                backend=DirectoryBackend.ACTIVE_DIRECTORY,
                differences=(
                    "Users and groups use AD object classes; username may mean sAMAccountName or userPrincipalName.",
                    "Account state uses userAccountControl; AD password changes require LDAPS or StartTLS.",
                ),
            ),
        ),
        safety_constraints=(
            "The local GGUF model proposes structured intents; it cannot execute directory writes.",
            "Mutations require a successful preview, a writable profile, and explicit confirmation.",
            "Ambiguous names or attribute changes require clarification rather than an arbitrary choice.",
            "Credential attributes cannot be requested or changed through model-generated intents.",
            "Searches without conditions require an explicit all-entries request; result limits apply.",
            "The server schema and access controls determine actual support; LDAP preview and write are not atomic.",
        ),
    )


def format_help_catalog(catalog: HelpCatalog) -> str:
    """Format catalog content as copyable human-readable help."""
    lines = ["Data Lens help", "", "Examples (model interpretations may vary):"]
    for item in catalog.intents:
        lines.append(f"- {item.operation}: {item.example}")
    lines.extend(["", "Directory information (no model required):"])
    for item in catalog.directory_information:
        lines.append(f"- {item.example} {item.description}")
    lines.extend(["", "Backend differences:"])
    for backend in catalog.backends:
        lines.append(f"- {backend.backend.value}:")
        lines.extend(f"  - {difference}" for difference in backend.differences)
    lines.extend(["", "Safety:"])
    lines.extend(f"- {constraint}" for constraint in catalog.safety_constraints)
    lines.extend(["", "Attribute aliases (subject to the server schema):"])
    for concept in catalog.aliases:
        lines.append(
            f"- {concept.label}: {', '.join(concept.phrases)}\n"
            f"  LDAP: {', '.join(concept.standard_ldap) or 'not mapped'}; "
            f"AD: {', '.join(concept.active_directory) or 'not mapped'}"
        )
    return "\n".join(lines)
