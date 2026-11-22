"""Everyday terms for directory attributes, per backend.

People ask for "email", "job title" or "who someone reports to"; directories store
``mail``, ``title`` and ``manager``, and Active Directory often uses different
attributes from standards-based LDAP. The registry maps phrases to *concepts*, and
each concept to its candidate attributes on each backend, most usual first.

A concept with several candidates is deliberately broad ("phone" covers the work,
mobile and home numbers). The resolver searches all of them, and asks which one is
meant before changing anything; the registry itself never picks.

The built-in table agrees with the older single-attribute tables in
``datasifter.directory.filters``; a test keeps the two consistent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import get_close_matches

from pydantic import Field

from datasifter.datalens.models import AccountState, DataLensModel, DirectoryBackend

_LEADING_WORDS = frozenset({"the", "a", "an", "their", "his", "her", "its", "my", "your"})


def normalize_term(term: str) -> str:
    """Reduce a phrase to a lookup key: lower case, letters and digits only.

    "E-mail address", "email_address" and "the Email Address" all become
    ``emailaddress``.
    """
    words = re.findall(r"[a-z0-9]+", term.casefold().replace("'s", ""))
    while words and words[0] in _LEADING_WORDS:
        words.pop(0)
    return "".join(words)


class AttributeConcept(DataLensModel):
    """One thing people ask about, and the attributes that hold it on each backend."""

    # Stable identifier, for example "email".
    key: str = Field(min_length=1)
    # How to name the concept in a question to the person.
    label: str = Field(min_length=1)
    # Everyday phrases that mean this concept.
    phrases: tuple[str, ...] = Field(min_length=1)
    # Candidate attributes, most usual first. Empty when the backend has none.
    standard_ldap: tuple[str, ...] = ()
    active_directory: tuple[str, ...] = ()

    def candidates(self, backend: DirectoryBackend) -> tuple[str, ...]:
        if backend is DirectoryBackend.ACTIVE_DIRECTORY:
            return self.active_directory
        return self.standard_ldap


def _concept(
    key: str,
    label: str,
    phrases: Iterable[str],
    standard: Iterable[str],
    active_directory: Iterable[str] | None = None,
) -> AttributeConcept:
    """Build a concept; ``active_directory`` defaults to the standard attributes."""
    standard = tuple(standard)
    return AttributeConcept(
        key=key,
        label=label,
        phrases=tuple(phrases),
        standard_ldap=standard,
        active_directory=standard if active_directory is None else tuple(active_directory),
    )


BUILT_IN_CONCEPTS: tuple[AttributeConcept, ...] = (
    # --- Identity ---------------------------------------------------------
    _concept(
        "email", "email address",
        ("email", "e-mail", "mail", "email address", "mail address", "primary email",
         "primary email address", "primary mail"),
        ("mail",),
    ),
    _concept(
        "email_aliases", "additional email addresses",
        ("email alias", "email aliases", "alternate email", "secondary email", "proxy address",
         "proxy addresses"),
        ("mailAlternateAddress",), ("proxyAddresses",),
    ),
    # Active Directory has two login names; which one is meant must be asked.
    _concept(
        "username", "username",
        ("username", "user name", "login", "login name", "logon", "logon name", "user id",
         "account name"),
        ("uid",), ("sAMAccountName", "userPrincipalName"),
    ),
    _concept(
        "user_principal_name", "user principal name",
        ("upn", "user principal name", "principal name"),
        (), ("userPrincipalName",),
    ),
    _concept(
        "sam_account_name", "pre-Windows 2000 logon name",
        ("sam", "sam account name", "short logon name", "pre-windows 2000 logon name"),
        (), ("sAMAccountName",),
    ),
    # --- Names ------------------------------------------------------------
    _concept("first_name", "first name",
             ("first name", "given name", "given", "forename"), ("givenName",)),
    _concept("last_name", "last name",
             ("last name", "surname", "family name", "sn"), ("sn",)),
    _concept("display_name", "display name", ("display name", "full name"), ("displayName",)),
    _concept("common_name", "common name", ("common name", "cn"), ("cn",)),
    _concept("name", "name", ("name", "names"), ("cn", "displayName")),
    _concept("initials", "initials", ("initials",), ("initials",)),
    # --- Contact ----------------------------------------------------------
    _concept("phone", "phone number",
             ("phone", "phones", "phone number", "phone numbers"),
             ("telephoneNumber", "mobile", "homePhone")),
    _concept("work_phone", "work phone",
             ("telephone", "telephone number", "tel", "work phone", "work number",
              "office phone", "business phone", "main phone", "desk phone"),
             ("telephoneNumber",)),
    _concept("mobile", "mobile phone",
             ("mobile", "mobile number", "mobile phone", "cell", "cell number", "cell phone",
              "cellular", "cellular phone", "cellular number", "cellular telephone"),
             ("mobile",)),
    _concept("home_phone", "home phone", ("home phone", "home number"), ("homePhone",)),
    _concept("fax", "fax number",
             ("fax", "fax number", "facsimile", "facsimile telephone number"),
             ("facsimileTelephoneNumber",)),
    _concept("pager", "pager", ("pager", "pager number"), ("pager",)),
    # --- Organization -----------------------------------------------------
    # Standards-based directories record a department in either attribute.
    _concept("department", "department",
             ("department", "dept", "team"), ("departmentNumber", "ou"), ("department",)),
    _concept("title", "job title",
             ("title", "job title", "role", "position"), ("title",)),
    _concept("employee_type", "employee type",
             ("employee type", "employment type", "worker type"), ("employeeType",)),
    _concept("employee_id", "employee ID",
             ("employee id", "employee number", "staff id", "staff number", "personnel number"),
             ("employeeNumber",), ("employeeID", "employeeNumber")),
    _concept("manager", "manager",
             ("manager", "reports to", "line manager", "supervisor", "boss"), ("manager",)),
    _concept("company", "company",
             ("company", "organization", "organisation", "employer"), ("o",), ("company",)),
    # --- Location ---------------------------------------------------------
    _concept("office", "office",
             ("office", "office name", "office location"), ("physicalDeliveryOfficeName",)),
    _concept("room", "room number", ("room", "room number"), ("roomNumber",)),
    _concept("location", "location", ("location",), ("l", "physicalDeliveryOfficeName")),
    _concept("city", "city", ("city", "town", "locality"), ("l",)),
    _concept("state", "state or province", ("state", "province", "region"), ("st",)),
    _concept("postal_code", "postal code",
             ("zip", "zip code", "postcode", "post code", "postal code"), ("postalCode",)),
    _concept("street", "street address",
             ("street", "street address"), ("street",), ("streetAddress",)),
    _concept("address", "address",
             ("address", "postal address", "mailing address"),
             ("street", "postalAddress"), ("streetAddress", "postalAddress")),
    # Active Directory keeps the country name and its two-letter code separately.
    _concept("country", "country", ("country",), ("c",), ("co", "c")),
    # --- Description ------------------------------------------------------
    _concept("description", "description", ("description", "desc"), ("description",)),
    _concept("notes", "notes",
             ("notes", "note", "comment", "comments"), ("description",), ("info",)),
    # --- Credentials ------------------------------------------------------
    _concept("password", "password",
             ("password", "passwd", "pwd", "user password"),
             ("userPassword",), ("unicodePwd",)),
    # --- Groups -----------------------------------------------------------
    _concept("members", "group members",
             ("member", "members", "group member", "group members"),
             ("member", "uniqueMember", "memberUid"), ("member",)),
    _concept("groups", "group memberships",
             ("groups", "group membership", "group memberships", "member of", "memberships"),
             ("memberOf", "isMemberOf"), ("memberOf",)),
    # --- Timestamps -------------------------------------------------------
    _concept("created", "creation time",
             ("created", "created on", "creation date", "date created", "create date"),
             ("createTimestamp",), ("whenCreated",)),
    _concept("modified", "last change time",
             ("modified", "last modified", "changed", "last changed"),
             ("modifyTimestamp",), ("whenChanged",)),
    _concept("last_login", "last sign-in time",
             ("last login", "last logon", "last sign in", "last signin"),
             ("ds-pwp-last-login-time", "pwdLastSuccess", "lastLoginTime"),
             ("lastLogonTimestamp",)),
    # --- POSIX ------------------------------------------------------------
    _concept("uid_number", "numeric user ID", ("uid number", "user number"), ("uidNumber",)),
    _concept("gid_number", "numeric group ID", ("gid number", "gid", "group id"),
             ("gidNumber",)),
    _concept("home_directory", "home directory",
             ("home directory", "home dir", "home folder"), ("homeDirectory",)),
    _concept("login_shell", "login shell", ("shell", "login shell"), ("loginShell",)),
)

# Words that describe whether an account can sign in. They are states, not attributes.
BUILT_IN_ACCOUNT_STATES: dict[str, AccountState] = {
    "disabled": AccountState.DISABLED,
    "deactivated": AccountState.DISABLED,
    "inactive": AccountState.DISABLED,
    "suspended": AccountState.DISABLED,
    "enabled": AccountState.ENABLED,
    "active": AccountState.ENABLED,
    "locked": AccountState.LOCKED,
    "locked out": AccountState.LOCKED,
}
# Phrases that introduce an account state as a value: "status is disabled".
ACCOUNT_STATE_TERMS = frozenset({"status", "accountstatus", "accountstate"})


class AliasRegistry:
    """Looks up the concept, and so the candidate attributes, behind an everyday term."""

    def __init__(self, concepts: Iterable[AttributeConcept] = BUILT_IN_CONCEPTS) -> None:
        self._concepts: dict[str, AttributeConcept] = {}
        self._by_phrase: dict[str, str] = {}
        self._states = {
            normalize_term(phrase): state for phrase, state in BUILT_IN_ACCOUNT_STATES.items()
        }
        for concept in concepts:
            self.register(concept)

    def register(self, concept: AttributeConcept, *, replace: bool = False) -> None:
        """Add a concept, for example a site-specific one.

        A phrase may belong to one concept only, so that a term never resolves
        differently depending on registration order. Pass ``replace=True`` to take
        over phrases, or a whole concept, that are already registered.
        """
        keys = {normalize_term(phrase) for phrase in concept.phrases}
        if "" in keys:
            raise ValueError(f"Concept {concept.key!r} has a phrase without letters or digits.")
        taken = {
            key: self._by_phrase[key]
            for key in keys
            if self._by_phrase.get(key, concept.key) != concept.key
        }
        if (taken or concept.key in self._concepts) and not replace:
            detail = (
                f"its phrases {sorted(taken)} belong to {sorted(set(taken.values()))}"
                if taken
                else "that key is already registered"
            )
            raise ValueError(f"Cannot register concept {concept.key!r}: {detail}.")
        # Drop the phrases of the concept being replaced, then claim the new ones.
        self._by_phrase = {
            key: owner for key, owner in self._by_phrase.items() if owner != concept.key
        }
        self._concepts[concept.key] = concept
        for key in keys:
            self._by_phrase[key] = concept.key

    def concept_for(self, term: str) -> AttributeConcept | None:
        """Return the concept an everyday term means, or None when it is not a known phrase."""
        key = self._by_phrase.get(normalize_term(term))
        return self._concepts[key] if key is not None else None

    def concepts(self) -> tuple[AttributeConcept, ...]:
        """Return the effective concepts and phrases, including site-specific registrations."""
        result = []
        for concept in self._concepts.values():
            phrases = tuple(
                phrase for phrase in concept.phrases
                if self._by_phrase.get(normalize_term(phrase)) == concept.key
            )
            if phrases:
                result.append(concept.model_copy(update={"phrases": phrases}))
        return tuple(result)

    def account_state_for(self, term: str) -> AccountState | None:
        """Return the account state a word such as "disabled" or "active" names."""
        return self._states.get(normalize_term(term))

    def label_for(self, attribute: str, backend: DirectoryBackend) -> str | None:
        """Return an everyday label for an attribute, when one concept means exactly it."""
        wanted = attribute.casefold()
        for concept in self._concepts.values():
            candidates = concept.candidates(backend)
            if len(candidates) == 1 and candidates[0].casefold() == wanted:
                return concept.label
        return None

    def near_concepts(self, term: str, limit: int = 3) -> list[AttributeConcept]:
        """Return concepts whose phrases are spelled almost like ``term`` ("emial")."""
        keys = get_close_matches(normalize_term(term), list(self._by_phrase), n=limit, cutoff=0.8)
        concepts: list[AttributeConcept] = []
        for key in keys:
            concept = self._concepts[self._by_phrase[key]]
            if concept not in concepts:
                concepts.append(concept)
        return concepts
