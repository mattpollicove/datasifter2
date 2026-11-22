"""Typed target-session commands for Data Lens.

Commands are matched against the whole request, so a search such as
"list profiles in ou=People" is still treated as a search.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from datasifter.config import DirectoryProfile

CONNECT = "connect"
DISCONNECT = "disconnect"
LIST_TARGETS = "list"
CURRENT_TARGET = "status"

_PREFIX = r"^\s*(?:please\s+)?"
_SUFFIX = r"\s*[.!?]*\s*$"
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (action, re.compile(_PREFIX + body + _SUFFIX, re.IGNORECASE))
    for action, body in (
        (CONNECT, r"switch(?:\s+(?:target|session))?\s+to\s+(?P<target>.+?)"),
        (CONNECT, r"connect(?:\s+(?:to\s+)?(?P<target>.+?))?"),
        (DISCONNECT, r"disconnect(?:\s+from\s+(?P<target>.+?))?"),
        (DISCONNECT, r"(?:close|end)\s+(?:the\s+)?(?:current\s+)?session"),
        (LIST_TARGETS, r"(?:list|show)\s+(?:all\s+)?(?:targets|profiles|sessions)"),
        (
            CURRENT_TARGET,
            r"(?:show\s+)?(?:the\s+)?(?:current|active)\s+(?:target|session)",
        ),
        (CURRENT_TARGET, r"(?:target|session)\s+status"),
    )
)
_TARGET_KEYWORD = re.compile(r"^(?:the\s+)?(?:profile|target|directory)\s+(.+)$", re.IGNORECASE)


class SessionCommandError(ValueError):
    """Raised when a session command names a target that cannot be resolved."""


@dataclass(frozen=True, slots=True)
class SessionCommand:
    action: str
    target: str = ""


def parse_session_command(request: str) -> SessionCommand | None:
    """Return the session command expressed by ``request``, or ``None``."""
    for action, pattern in _PATTERNS:
        match = pattern.match(request)
        if match:
            target = match.groupdict().get("target") or ""
            return SessionCommand(action, target.strip().strip("\"'").strip())
    return None


def describe_target(profile: DirectoryProfile) -> str:
    return (
        f"{profile.name} — {profile.host}:{profile.port} "
        f"({profile.transport}, {profile.authentication_mode.replace('_', ' ')})"
    )


def resolve_target(target: str, profiles: Sequence[DirectoryProfile]) -> DirectoryProfile:
    """Match ``target`` to one saved profile by name, then host, then partial name."""
    if not profiles:
        raise SessionCommandError("No saved profiles. Use Add profile to create a target.")
    candidates = [target.casefold()]
    keyword = _TARGET_KEYWORD.match(target)
    if keyword:
        candidates.append(keyword.group(1).strip().strip("\"'").strip().casefold())
    matchers = (
        lambda profile, text: profile.name.casefold() == text,
        lambda profile, text: profile.host.casefold() == text,
        lambda profile, text: text in profile.name.casefold(),
    )
    for matches in matchers:
        for text in candidates:
            found = [profile for profile in profiles if matches(profile, text)]
            if len(found) == 1:
                return found[0]
            if found:
                names = "\n".join(describe_target(profile) for profile in found)
                raise SessionCommandError(
                    f"More than one saved profile matches '{target}'. "
                    f"Use a more specific name:\n{names}"
                )
    raise SessionCommandError(
        f"No saved profile matches '{target}'. Type \"list targets\" to see saved profiles."
    )
