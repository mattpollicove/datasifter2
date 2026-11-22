"""Adapter for standards-based LDAP v3 directories.

Covers servers that follow the RFC 4519 / RFC 2798 / RFC 2307 schemas, such as
OpenLDAP, 389 Directory Server, PingDirectory and OpenDJ: people are
``inetOrgPerson`` (optionally ``posixAccount``) entries identified by ``uid`` or
``cn``, and groups list their members on the group entry.
"""

from __future__ import annotations

from threading import Event

from datasifter.datalens.adapters.base import DirectoryAdapter
from datasifter.datalens.models import (
    AccountState,
    AccountStatus,
    AttributeChange,
    ChangeOperation,
    DirectoryBackend,
    DirectoryEntry,
)

# Group object class (lower case) -> the attribute that holds its members.
_MEMBER_ATTRIBUTES = {
    "groupofuniquenames": "uniqueMember",
    # posixGroup members are login names (uid values), not DNs.
    "posixgroup": "memberUid",
}

# Operational attributes with which common servers mark an account as disabled.
# There is no standard for this, so each server family has its own.
_DISABLED_ATTRIBUTES = (
    "nsAccountLock",  # 389 Directory Server, FreeIPA
    "ds-pwp-account-disabled",  # PingDirectory, OpenDJ
    "loginDisabled",  # eDirectory
)
# Password-policy lockout time (draft-behera-ldap-password-policy).
_LOCKED_ATTRIBUTE = "pwdAccountLockedTime"


class StandardLdapAdapter(DirectoryAdapter):
    backend = DirectoryBackend.STANDARD_LDAP
    user_object_classes = ("top", "person", "organizationalPerson", "inetOrgPerson")
    group_object_classes = ("top", "groupOfNames")
    identity_attributes = ("uid", "cn", "mail")
    group_member_attribute = "member"
    account_status_attributes = (*_DISABLED_ATTRIBUTES, _LOCKED_ATTRIBUTE)
    system_attributes = frozenset(
        {
            "createtimestamp",
            "modifytimestamp",
            "creatorsname",
            "modifiersname",
            "entryuuid",
            "entrydn",
            "entrycsn",
            "structuralobjectclass",
            "subschemasubentry",
            "hassubordinates",
            "numsubordinates",
            # Maintained from the group side by the server.
            "memberof",
            "ismemberof",
        }
    )

    def _user_class_filter(self) -> str:
        # inetOrgPerson entries are also "person"; posixAccount may be used without it.
        return "(|(objectClass=person)(objectClass=posixAccount))"

    def _group_class_filter(self) -> str:
        return (
            "(|(objectClass=groupOfNames)(objectClass=groupOfUniqueNames)"
            "(objectClass=posixGroup))"
        )

    def _disabling_attributes(self, cancelled: Event | None) -> list[str]:
        """Return the account-disabling attributes this server's schema defines."""
        schema = self.schema(cancelled=cancelled)
        if schema is None:
            return []
        return [name for name in _DISABLED_ATTRIBUTES if schema.knows_attribute(name)]

    def account_state_filter(
        self, state: AccountState, *, cancelled: Event | None = None
    ) -> str | None:
        if state is AccountState.LOCKED:
            schema = self.schema(cancelled=cancelled)
            if schema is None or not schema.knows_attribute(_LOCKED_ATTRIBUTE):
                return None
            return f"({_LOCKED_ATTRIBUTE}=*)"
        attributes = self._disabling_attributes(cancelled)
        if not attributes:
            return None
        tests = [f"({attribute}=TRUE)" for attribute in attributes]
        disabled = tests[0] if len(tests) == 1 else f"(|{''.join(tests)})"
        return disabled if state is AccountState.DISABLED else f"(!{disabled})"

    def account_state_change(
        self, entry: DirectoryEntry, *, disabled: bool, cancelled: Event | None = None
    ) -> AttributeChange | None:
        attributes = self._disabling_attributes(cancelled)
        if not attributes:
            return None
        # Replacing with no values removes the flag, and succeeds when it was never set.
        return AttributeChange(
            attribute=attributes[0],
            operation=ChangeOperation.REPLACE,
            values=(True,) if disabled else (),
        )

    def member_attribute(self, group: DirectoryEntry) -> str:
        for object_class, attribute in _MEMBER_ATTRIBUTES.items():
            if group.has_object_class(object_class):
                return attribute
        return self.group_member_attribute

    def account_status(self, entry: DirectoryEntry) -> AccountStatus:
        """Report what the entry's operational attributes say about the account.

        These attributes are only present when set, and only returned when asked
        for, so their absence proves nothing: ``enabled`` and ``locked`` stay
        ``None`` unless an attribute gives evidence.
        """
        flags: list[str] = []
        enabled: bool | None = None
        for attribute in _DISABLED_ATTRIBUTES:
            value = entry.first(attribute)
            if value is None:
                continue
            disabled = str(value).casefold() == "true"
            enabled = not disabled if enabled is None else enabled and not disabled
            if disabled:
                flags.append(attribute)
        locked: bool | None = None
        if entry.first(_LOCKED_ATTRIBUTE) is not None:
            locked = True
            flags.append(_LOCKED_ATTRIBUTE)
        return AccountStatus(enabled=enabled, locked=locked, flags=tuple(flags))
