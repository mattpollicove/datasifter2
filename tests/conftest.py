"""Shared fixtures: the in-memory mock directory and the non-production live directories.

Each live target is described by a git-ignored ``tests/*.local.json`` file. Tests
that need a target are skipped when its file is absent, and fail when the file names
a target that cannot be reached, so an outage is never mistaken for a pass.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import keyring
import pytest
from datalens_support import MockDirectory

from datasifter.config import DirectoryProfile
from datasifter.datalens import DirectoryBackend
from datasifter.directory.service import LDAPService
from datasifter.security.certificates import CertificateTrustStore

LIVE_KEYRING_SERVICE = "DataSifter live tests"


def load_live_target(file_name: str, password_variable: str) -> dict[str, Any]:
    """Return the settings and bind password of a live target, or skip the test."""
    path = Path(__file__).with_name(file_name)
    if not path.exists():
        example = file_name.replace(".local.", ".example.")
        pytest.skip(
            f"No live target: copy {example} to {file_name} and store the bind password."
        )
    settings = json.loads(path.read_text(encoding="utf-8"))
    password = os.environ.get(password_variable) or keyring.get_password(
        LIVE_KEYRING_SERVICE, f"{settings['bind_dn']}@{settings['host']}"
    )
    if not password:
        pytest.fail(
            f"{file_name} names a live target but no bind password is in "
            f"{password_variable} or the OS keyring."
        )
    try:
        socket.create_connection((settings["host"], settings["ldap_port"]), timeout=5).close()
    except OSError as error:
        pytest.fail(
            f"Live target {settings['host']}:{settings['ldap_port']} is unreachable: {error}"
        )
    return {**settings, "password": password}


@pytest.fixture(scope="session")
def live_settings() -> dict[str, Any]:
    """The standards-based LDAP test directory."""
    return load_live_target("live_ldap.local.json", "DATASIFTER_LIVE_LDAP_PASSWORD")


@pytest.fixture(scope="session")
def live_ad_settings() -> dict[str, Any]:
    """The Active Directory test domain."""
    return load_live_target("live_ad.local.json", "DATASIFTER_LIVE_AD_PASSWORD")


@pytest.fixture
def build_live_service(tmp_path, monkeypatch) -> Callable[..., LDAPService]:
    """Return a factory for services bound to a live target over a chosen transport."""

    def build(
        settings: dict[str, Any], transport: str = "plain", read_only: bool = False
    ) -> LDAPService:
        address = settings["host"]
        host = address
        server_name = settings.get("tls_server_name")
        if transport != "plain" and server_name:
            # The certificate names the server, not its address: resolve that name
            # to the target here, as a hosts-file entry would.
            resolve = socket.getaddrinfo
            monkeypatch.setattr(
                socket,
                "getaddrinfo",
                lambda name, *args, **kwargs: resolve(
                    address if name == server_name else name, *args, **kwargs
                ),
            )
            host = server_name
        profile = DirectoryProfile(
            name="live",
            host=host,
            port=settings["ldaps_port" if transport == "ldaps" else "ldap_port"],
            transport=transport,
            authentication_mode="simple",
            bind_dn=settings["bind_dn"],
            base_dn=settings["base_dn"],
            read_only=read_only,
        )
        trust_store = CertificateTrustStore(tmp_path / "certificates")
        if transport != "plain":
            trust_store.accept_tofu(profile, LDAPService.inspect_server_certificate(profile))
        return LDAPService(profile, settings["password"], trust_store=trust_store)

    return build


@pytest.fixture
def make_directory(monkeypatch, tmp_path) -> Callable[[DirectoryBackend], MockDirectory]:
    """Return a factory for in-memory directories with an OpenLDAP or AD schema."""
    return lambda backend: MockDirectory(backend, monkeypatch, tmp_path)


@pytest.fixture(params=list(DirectoryBackend), ids=lambda backend: backend.value)
def directory(request, make_directory) -> MockDirectory:
    """An in-memory directory; the test runs once per backend."""
    return make_directory(request.param)


@pytest.fixture
def standard(make_directory) -> MockDirectory:
    return make_directory(DirectoryBackend.STANDARD_LDAP)


@pytest.fixture
def active_directory(make_directory) -> MockDirectory:
    return make_directory(DirectoryBackend.ACTIVE_DIRECTORY)
