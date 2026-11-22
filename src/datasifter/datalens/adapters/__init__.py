"""Directory backends for the Data Lens engine.

Use :func:`create_adapter` with an explicit backend. :func:`detect_backend` is a
helper for callers that do not know which kind of server a profile points at.
"""

from __future__ import annotations

from threading import Event

from datasifter.datalens.adapters.active_directory import ActiveDirectoryAdapter
from datasifter.datalens.adapters.base import DirectoryAdapter
from datasifter.datalens.adapters.standard import StandardLdapAdapter
from datasifter.datalens.models import DirectoryBackend
from datasifter.directory.service import LDAPService

# RootDSE supportedCapabilities values that only Microsoft directories advertise:
# Active Directory Domain Services and AD Lightweight Directory Services.
_ACTIVE_DIRECTORY_CAPABILITIES = frozenset(
    {"1.2.840.113556.1.4.800", "1.2.840.113556.1.4.1851"}
)

_ADAPTERS: dict[DirectoryBackend, type[DirectoryAdapter]] = {
    DirectoryBackend.STANDARD_LDAP: StandardLdapAdapter,
    DirectoryBackend.ACTIVE_DIRECTORY: ActiveDirectoryAdapter,
}


def create_adapter(
    service: LDAPService, backend: DirectoryBackend, *, read_only: bool = False
) -> DirectoryAdapter:
    """Return the adapter for ``backend`` on top of an existing LDAP service."""
    return _ADAPTERS[DirectoryBackend(backend)](service, read_only=read_only)


def detect_backend(service: LDAPService, cancelled: Event | None = None) -> DirectoryBackend:
    """Read the server's RootDSE and report which backend it is.

    Anything that does not advertise an Active Directory capability is treated as
    a standards-based directory.
    """
    root = service.search(
        "", "(objectClass=*)", attributes=["*", "+"], scope="base", cancelled=cancelled
    )
    capabilities: set[str] = set()
    for entry in root:
        for name, values in entry["attributes"].items():
            if name.casefold() == "supportedcapabilities":
                capabilities.update(str(value) for value in values)
    if capabilities & _ACTIVE_DIRECTORY_CAPABILITIES:
        return DirectoryBackend.ACTIVE_DIRECTORY
    return DirectoryBackend.STANDARD_LDAP


__all__ = [
    "ActiveDirectoryAdapter",
    "DirectoryAdapter",
    "StandardLdapAdapter",
    "create_adapter",
    "detect_backend",
]
