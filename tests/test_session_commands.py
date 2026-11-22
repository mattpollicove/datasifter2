import pytest

from datasifter.config import DirectoryProfile
from datasifter.session_commands import (
    CONNECT,
    CURRENT_TARGET,
    DISCONNECT,
    LIST_TARGETS,
    SessionCommand,
    SessionCommandError,
    parse_session_command,
    resolve_target,
)


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("connect", SessionCommand(CONNECT)),
        ("Connect to Lab LDAP", SessionCommand(CONNECT, "Lab LDAP")),
        ('please switch to "Lab LDAP".', SessionCommand(CONNECT, "Lab LDAP")),
        ("switch target to lab", SessionCommand(CONNECT, "lab")),
        ("disconnect", SessionCommand(DISCONNECT)),
        ("disconnect from lab", SessionCommand(DISCONNECT, "lab")),
        ("end session", SessionCommand(DISCONNECT)),
        ("list targets", SessionCommand(LIST_TARGETS)),
        ("show all profiles", SessionCommand(LIST_TARGETS)),
        ("current target", SessionCommand(CURRENT_TARGET)),
        ("show the active session?", SessionCommand(CURRENT_TARGET)),
        ("session status", SessionCommand(CURRENT_TARGET)),
        ("list profiles in ou=People", None),
        ("show sessions for mattp", None),
        ("disconnected accounts", None),
        ("switch", None),
        ("surname starts with b", None),
    ],
)
def test_parse_session_command(request_text, expected):
    assert parse_session_command(request_text) == expected


def test_resolve_target_prefers_exact_name_then_host_then_partial_name():
    lab = DirectoryProfile(name="Lab", host="ldap.lab.test")
    lab_copy = DirectoryProfile(name="Lab replica", host="replica.lab.test")
    production = DirectoryProfile(name="Production", host="ldap.example.test")
    profiles = [lab, lab_copy, production]

    assert resolve_target("lab", profiles) is lab
    assert resolve_target("REPLICA.lab.test", profiles) is lab_copy
    assert resolve_target("prod", profiles) is production
    assert resolve_target("profile production", profiles) is production


def test_resolve_target_reports_ambiguous_and_unknown_targets():
    profiles = [
        DirectoryProfile(name="Lab east", host="east.test"),
        DirectoryProfile(name="Lab west", host="west.test"),
    ]

    with pytest.raises(SessionCommandError, match="More than one saved profile") as error:
        resolve_target("lab", profiles)
    assert "Lab east" in str(error.value)
    assert "Lab west" in str(error.value)
    with pytest.raises(SessionCommandError, match="No saved profile matches 'nowhere'"):
        resolve_target("nowhere", profiles)
    with pytest.raises(SessionCommandError, match="No saved profiles"):
        resolve_target("lab", [])
