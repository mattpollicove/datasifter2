"""Distinguished-name helpers shared by the Data Lens models and adapters.

Parsing is delegated to ``ldap3`` so that escaping rules (RFC 4514) are applied the
same way here as when a request is sent. Everything in this module is pure string
handling; nothing contacts a directory.
"""

from __future__ import annotations

import string

from ldap3.core.exceptions import LDAPInvalidDnError
from ldap3.utils.dn import parse_dn


class InvalidDnError(ValueError):
    """Raised when text is not a usable distinguished name or relative name."""


# One relative distinguished name: its (attribute type, escaped value) pairs.
# A multi-valued RDN such as ``cn=a+uid=b`` has two pairs.
_Rdn = tuple[tuple[str, str], ...]


def _parse(distinguished_name: str) -> list[_Rdn]:
    """Split a DN into RDNs, most specific first, keeping values escaped."""
    if not isinstance(distinguished_name, str) or not distinguished_name.strip():
        raise InvalidDnError("A distinguished name cannot be empty.")
    try:
        components = parse_dn(distinguished_name, escape=False, strip=False)
    except LDAPInvalidDnError:
        # Tolerate insignificant spaces around separators ("cn=a, dc=b"), which
        # people type and older tools emit; the rebuilt DN drops them.
        try:
            components = parse_dn(distinguished_name, escape=False, strip=True)
        except LDAPInvalidDnError as error:
            raise InvalidDnError(
                f"Not a valid distinguished name: {distinguished_name!r} ({error})"
            ) from error
    rdns: list[_Rdn] = []
    current: list[tuple[str, str]] = []
    for attribute, value, separator in components:
        current.append((attribute, value))
        if separator != "+":
            rdns.append(tuple(current))
            current = []
    if current:
        raise InvalidDnError(f"Not a valid distinguished name: {distinguished_name!r}")
    return rdns


def _format_rdn(rdn: _Rdn) -> str:
    return "+".join(f"{attribute}={value}" for attribute, value in rdn)


def _format(rdns: list[_Rdn]) -> str:
    return ",".join(_format_rdn(rdn) for rdn in rdns)


def _unescape(value: str) -> str:
    """Return the attribute value an escaped RDN value stands for."""
    raw = bytearray()
    index = 0
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value):
            pair = value[index + 1 : index + 3]
            if len(pair) == 2 and all(digit in string.hexdigits for digit in pair):
                raw.append(int(pair, 16))
                index += 3
                continue
            raw.extend(value[index + 1].encode("utf-8"))
            index += 2
            continue
        raw.extend(character.encode("utf-8"))
        index += 1
    return raw.decode("utf-8", errors="replace")


def normalize_dn(distinguished_name: str) -> str:
    """Validate a DN and return it without insignificant whitespace.

    Case and escaping are preserved, because some directories store the DN as written.
    """
    return _format(_parse(distinguished_name))


def normalize_rdn(relative_name: str) -> str:
    """Validate a single RDN such as ``cn=Ada Lane`` and return it normalized."""
    rdns = _parse(relative_name)
    if len(rdns) != 1:
        raise InvalidDnError(
            f"Expected one relative name such as 'cn=Ada Lane', not {relative_name!r}."
        )
    return _format_rdn(rdns[0])


def split_dn(distinguished_name: str) -> tuple[str, str]:
    """Return ``(rdn, parent)``; the parent is empty for a one-component DN."""
    rdns = _parse(distinguished_name)
    return _format_rdn(rdns[0]), _format(rdns[1:])


def join_dn(relative_name: str, parent: str) -> str:
    """Build the DN of ``relative_name`` directly beneath ``parent``."""
    rdn = normalize_rdn(relative_name)
    return f"{rdn},{normalize_dn(parent)}" if parent else rdn


def rdn_pairs(distinguished_name: str) -> tuple[tuple[str, str], ...]:
    """Return the ``(attribute, value)`` pairs of a DN's first RDN, unescaped."""
    return tuple(
        (attribute, _unescape(value)) for attribute, value in _parse(distinguished_name)[0]
    )


def _key(rdns: list[_Rdn]) -> tuple[tuple[tuple[str, str], ...], ...]:
    return tuple(
        tuple(sorted((attribute.casefold(), _unescape(value).casefold()) for attribute, value in rdn))
        for rdn in rdns
    )


def same_dn(first: str, second: str) -> bool:
    """Compare two DNs ignoring case, spacing and escaping style.

    This is a client-side approximation: attributes with case-sensitive matching
    rules are still treated as equal. The directory remains the authority.
    """
    return _key(_parse(first)) == _key(_parse(second))


def is_descendant(distinguished_name: str, ancestor: str) -> bool:
    """Return True when ``distinguished_name`` lies strictly beneath ``ancestor``."""
    child = _key(_parse(distinguished_name))
    parent = _key(_parse(ancestor))
    return len(child) > len(parent) and child[len(child) - len(parent) :] == parent


def is_same_or_descendant(distinguished_name: str, ancestor: str) -> bool:
    return same_dn(distinguished_name, ancestor) or is_descendant(distinguished_name, ancestor)


def dn_depth(distinguished_name: str) -> int:
    """Count the RDNs of a DN without validating it.

    Safe for DNs returned by a server, which are trusted as written and may use
    forms the strict parser rejects.
    """
    depth = 1
    escaped = False
    for character in distinguished_name:
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ",":
            depth += 1
    return depth
