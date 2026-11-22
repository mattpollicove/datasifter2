"""Adapter for Microsoft Active Directory Domain Services.

Active Directory speaks LDAP v3 but differs from standards-based directories in
ways that matter before a write is sent. The rules encoded here were checked
against a Windows Server domain controller:

* People are ``user`` entries named by ``CN`` and identified by ``sAMAccountName``
  and ``userPrincipalName``; groups are ``group`` entries. ``memberOf`` is computed
  from the groups' ``member`` attribute and cannot be written.
* Account state lives in the ``userAccountControl`` bit field. An account created
  without a password is disabled, and cannot be enabled until it has one.
* Passwords are written to ``unicodePwd`` as a quoted UTF-16LE string, and only
  over an encrypted connection.
* A rename must delete the old RDN and keep the naming attribute type.
* Objects flagged ``isCriticalSystemObject`` belong to the domain itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from threading import Event
from typing import Any

from datasifter.datalens.adapters.base import DirectoryAdapter, IssueCollector
from datasifter.datalens.dn import rdn_pairs
from datasifter.datalens.models import (
    AccountState,
    AccountStatus,
    AttributeChange,
    ChangeOperation,
    CreatePlan,
    DeletePlan,
    DirectoryBackend,
    DirectoryEntry,
    IssueCode,
    MovePlan,
    UpdatePlan,
    WritableValue,
    base_attribute_name,
)

# userAccountControl bits (MS-ADTS 2.2.16), lowest bit first.
USER_ACCOUNT_CONTROL_FLAGS: dict[int, str] = {
    0x00000001: "SCRIPT",
    0x00000002: "ACCOUNTDISABLE",
    0x00000008: "HOMEDIR_REQUIRED",
    0x00000010: "LOCKOUT",
    0x00000020: "PASSWD_NOTREQD",
    0x00000040: "PASSWD_CANT_CHANGE",
    0x00000080: "ENCRYPTED_TEXT_PWD_ALLOWED",
    0x00000100: "TEMP_DUPLICATE_ACCOUNT",
    0x00000200: "NORMAL_ACCOUNT",
    0x00000800: "INTERDOMAIN_TRUST_ACCOUNT",
    0x00001000: "WORKSTATION_TRUST_ACCOUNT",
    0x00002000: "SERVER_TRUST_ACCOUNT",
    0x00010000: "DONT_EXPIRE_PASSWORD",
    0x00020000: "MNS_LOGON_ACCOUNT",
    0x00040000: "SMARTCARD_REQUIRED",
    0x00080000: "TRUSTED_FOR_DELEGATION",
    0x00100000: "NOT_DELEGATED",
    0x00200000: "USE_DES_KEY_ONLY",
    0x00400000: "DONT_REQ_PREAUTH",
    0x00800000: "PASSWORD_EXPIRED",
    0x01000000: "TRUSTED_TO_AUTH_FOR_DELEGATION",
    0x04000000: "PARTIAL_SECRETS_ACCOUNT",
}
ACCOUNT_DISABLED = 0x00000002
# LDAP_MATCHING_RULE_BIT_AND: true when every bit of the assertion value is set.
_BIT_AND_RULE = "1.2.840.113556.1.4.803"
PASSWORD_NOT_REQUIRED = 0x00000020

# The longest sAMAccountName the domain accepts for a user (pre-Windows 2000 logon name).
_MAX_USER_SAM_ACCOUNT_NAME = 20
# Object classes (lower case) whose entries must be named by CN.
_CN_NAMED_CLASSES = frozenset({"user", "group", "contact", "computer", "container"})


def decode_user_account_control(value: int) -> tuple[str, ...]:
    """Return the names of the flags set in a ``userAccountControl`` value."""
    return tuple(name for bit, name in USER_ACCOUNT_CONTROL_FLAGS.items() if value & bit)


def encode_unicode_pwd(password: str) -> bytes:
    """Encode a password the way ``unicodePwd`` requires: quoted, UTF-16 little-endian."""
    return f'"{password}"'.encode("utf-16-le")


def _is_true(value: Any) -> bool:
    return value is True or str(value).casefold() == "true"


class ActiveDirectoryAdapter(DirectoryAdapter):
    backend = DirectoryBackend.ACTIVE_DIRECTORY
    user_object_classes = ("top", "person", "organizationalPerson", "user")
    group_object_classes = ("top", "group")
    identity_attributes = ("sAMAccountName", "userPrincipalName", "cn", "mail")
    group_identity_attributes = ("cn", "sAMAccountName")
    group_member_attribute = "member"
    container_classes = (("organizationalUnit", "ou"), ("container", "cn"))
    account_status_attributes = ("userAccountControl", "lockoutTime")
    system_attributes = frozenset(
        {
            "objectguid",
            "objectsid",
            "distinguishedname",
            "canonicalname",
            "name",
            "whencreated",
            "whenchanged",
            "usncreated",
            "usnchanged",
            "createtimestamp",
            "modifytimestamp",
            "instancetype",
            "samaccounttype",
            "primarygrouptoken",
            "iscriticalsystemobject",
            "dscorepropagationdata",
            "badpwdcount",
            "badpasswordtime",
            "lastlogon",
            "lastlogontimestamp",
            "lastlogoff",
            "logoncount",
            "tokengroups",
            "structuralobjectclass",
            "subschemasubentry",
            # Computed from each group's member attribute.
            "memberof",
        }
    )
    # The schema lists these as required, but the domain controller supplies them.
    server_supplied_attributes = frozenset(
        {
            "instancetype",
            "ntsecuritydescriptor",
            "objectcategory",
            "objectsid",
            "samaccountname",
            "grouptype",
        }
    )
    _descendant_attributes = ("isCriticalSystemObject",)

    def _user_class_filter(self) -> str:
        # Computer accounts are also objectClass=user; objectCategory excludes them.
        return "(&(objectCategory=person)(objectClass=user))"

    def _group_class_filter(self) -> str:
        return "(objectClass=group)"

    # ------------------------------------------------------------------
    # Account state
    # ------------------------------------------------------------------
    def account_state_filter(
        self, state: AccountState, *, cancelled: Event | None = None
    ) -> str | None:
        if state is AccountState.LOCKED:
            return "(lockoutTime>=1)"
        disabled = f"(userAccountControl:{_BIT_AND_RULE}:={ACCOUNT_DISABLED})"
        return disabled if state is AccountState.DISABLED else f"(!{disabled})"

    def account_state_change(
        self, entry: DirectoryEntry, *, disabled: bool, cancelled: Event | None = None
    ) -> AttributeChange | None:
        return self.account_control_change(entry, disabled=disabled)

    def account_status(self, entry: DirectoryEntry) -> AccountStatus:
        control = entry.first("userAccountControl")
        enabled: bool | None = None
        flags: tuple[str, ...] = ()
        if control is not None:
            value = int(control)
            enabled = not value & ACCOUNT_DISABLED
            flags = decode_user_account_control(value)
        # The LOCKOUT bit is not maintained in userAccountControl; lockoutTime is
        # the reliable signal. Zero (shown by ldap3 as the year 1601) means not locked.
        lockout = entry.first("lockoutTime")
        locked: bool | None = None
        if isinstance(lockout, datetime):
            locked = lockout.year > 1601
        elif lockout is not None:
            locked = int(lockout) > 0
        return AccountStatus(enabled=enabled, locked=locked, flags=flags)

    def account_control_change(self, entry: DirectoryEntry, *, disabled: bool) -> AttributeChange:
        """Return the change that disables or enables the account, keeping its other flags.

        The entry must have been read with ``userAccountControl``.
        """
        control = entry.first("userAccountControl")
        if control is None:
            raise ValueError(f"{entry.dn} was read without userAccountControl.")
        value = int(control)
        value = value | ACCOUNT_DISABLED if disabled else value & ~ACCOUNT_DISABLED
        return AttributeChange(
            attribute="userAccountControl", operation=ChangeOperation.REPLACE, values=(value,)
        )

    # ------------------------------------------------------------------
    # Backend rules
    # ------------------------------------------------------------------
    def _validate_create(
        self,
        plan: CreatePlan,
        attributes: Mapping[str, tuple[WritableValue, ...]],
        issues: IssueCollector,
    ) -> None:
        values = {name.casefold(): entry_values for name, entry_values in attributes.items()}
        classes = {str(value).casefold() for value in values.get("objectclass", ())}

        naming_attributes = {attribute.casefold() for attribute, _ in rdn_pairs(plan.dn)}
        expected = None
        if "organizationalunit" in classes:
            expected = "ou"
        elif classes & _CN_NAMED_CLASSES:
            expected = "cn"
        if expected is not None and naming_attributes != {expected}:
            issues.error(
                IssueCode.RDN_ATTRIBUTE,
                f"Active Directory names this kind of entry by {expected.upper()}; "
                f"use a DN that starts with {expected.upper()}=.",
            )

        has_password = self._check_password_attributes(values, issues)
        if "user" in classes and "computer" not in classes:
            self._check_new_user(values, has_password, issues)

    def _check_new_user(
        self,
        values: Mapping[str, tuple[WritableValue, ...]],
        has_password: bool,
        issues: IssueCollector,
    ) -> None:
        account_name = values.get("samaccountname")
        if account_name is None:
            issues.warning(
                IssueCode.MISSING_REQUIRED_ATTRIBUTE,
                "No sAMAccountName was given; Active Directory will generate a random one.",
                attribute="sAMAccountName",
            )
        elif len(str(account_name[0])) > _MAX_USER_SAM_ACCOUNT_NAME:
            issues.error(
                IssueCode.INVALID_VALUE,
                f"sAMAccountName is limited to {_MAX_USER_SAM_ACCOUNT_NAME} characters "
                "for a user.",
                attribute="sAMAccountName",
            )
        if has_password:
            return
        control = values.get("useraccountcontrol")
        if control is None:
            issues.warning(
                IssueCode.ACCOUNT_STATE,
                "No password was given, so the account will be created disabled.",
            )
            return
        try:
            flags = int(control[0])
        except (TypeError, ValueError):
            issues.error(
                IssueCode.INVALID_VALUE,
                "userAccountControl must be a whole number.",
                attribute="userAccountControl",
            )
            return
        if not flags & ACCOUNT_DISABLED and not flags & PASSWORD_NOT_REQUIRED:
            issues.error(
                IssueCode.ACCOUNT_STATE,
                "Active Directory refuses to create an enabled account without a password.",
                attribute="userAccountControl",
            )

    def _validate_update(
        self,
        plan: UpdatePlan,
        before: DirectoryEntry,
        after: DirectoryEntry,
        issues: IssueCollector,
    ) -> None:
        self._check_password_attributes(
            {base_attribute_name(change.attribute): change.values for change in plan.changes},
            issues,
        )

    def _check_password_attributes(
        self, values: Mapping[str, Sequence[WritableValue]], issues: IssueCollector
    ) -> bool:
        """Report misuse of password attributes; return True when unicodePwd is written."""
        if "userpassword" in values:
            # On a default domain userPassword is an ordinary attribute, so the
            # password would be stored readable and would not let anyone sign in.
            issues.error(
                IssueCode.UNSUPPORTED,
                "Active Directory sets passwords through unicodePwd, not userPassword.",
                attribute="userPassword",
            )
        if "unicodepwd" not in values:
            return False
        if self._service.profile.transport == "plain":
            issues.error(
                IssueCode.INSECURE_TRANSPORT,
                "Active Directory accepts passwords only over LDAPS or StartTLS; this "
                "profile uses plain LDAP.",
                attribute="unicodePwd",
            )
        return True

    def _validate_delete(
        self,
        plan: DeletePlan,
        before: DirectoryEntry,
        descendants: Sequence[DirectoryEntry],
        issues: IssueCollector,
    ) -> None:
        critical = [
            entry.dn
            for entry in (before, *descendants)
            if _is_true(entry.first("isCriticalSystemObject"))
        ]
        if critical:
            listed = ", ".join(critical[:3]) + (" and others" if len(critical) > 3 else "")
            issues.error(
                IssueCode.PROTECTED_ENTRY,
                f"Critical system objects cannot be deleted: {listed}.",
            )

    def _validate_move(
        self,
        plan: MovePlan,
        before: DirectoryEntry,
        current_rdn: str,
        target_rdn: str,
        issues: IssueCollector,
    ) -> None:
        if _is_true(before.first("isCriticalSystemObject")):
            issues.error(
                IssueCode.PROTECTED_ENTRY,
                f"{before.dn} is a critical system object and cannot be renamed or moved.",
            )
        if target_rdn == current_rdn:
            return
        current_types = {attribute.casefold() for attribute, _ in rdn_pairs(current_rdn)}
        target_types = {attribute.casefold() for attribute, _ in rdn_pairs(target_rdn)}
        if current_types != target_types:
            issues.error(
                IssueCode.RDN_ATTRIBUTE,
                "Active Directory cannot change the naming attribute of an entry; keep "
                f"{'+'.join(sorted(current_types)).upper()}= in the new name.",
            )
        if not plan.delete_old_rdn:
            issues.error(
                IssueCode.UNSUPPORTED,
                "Active Directory requires the old name to be removed when an entry is renamed.",
            )

    def _wire_values(self, attribute: str, values: Sequence[WritableValue]) -> list[Any]:
        if base_attribute_name(attribute) == "unicodepwd":
            # Bytes are taken to be encoded already.
            return [
                encode_unicode_pwd(value) if isinstance(value, str) else value for value in values
            ]
        return super()._wire_values(attribute, values)
