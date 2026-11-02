"""Local profile and preference storage."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import keyring


class ConfigurationError(RuntimeError):
    """Raised when local configuration or credential storage cannot be used safely."""


def configuration_directory() -> Path:
    if os.name == "nt":
        app_data = os.environ.get("APPDATA")
        if not app_data:
            raise ConfigurationError("APPDATA is not set; cannot determine the profile directory.")
        return Path(app_data) / "DataSifter"
    return Path.home() / ".config" / "datasifter"


@dataclass(slots=True)
class DirectoryProfile:
    name: str
    host: str
    port: int = 389
    transport: str = "plain"
    authentication_mode: str = "anonymous"
    bind_dn: str = ""
    base_dn: str = ""
    ca_certificate: str = ""
    client_certificate: str = ""
    client_key: str = ""
    tofu_fingerprint: str = ""
    profile_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    read_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Profile name cannot be empty.")
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError("Directory host cannot be empty.")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        if not isinstance(self.transport, str) or self.transport not in {
            "plain",
            "starttls",
            "ldaps",
        }:
            raise ValueError("Transport must be plain, starttls, or ldaps.")
        if not isinstance(self.authentication_mode, str) or self.authentication_mode not in {
            "anonymous",
            "simple",
            "sasl_external",
        }:
            raise ValueError("Authentication must be anonymous, simple, or SASL EXTERNAL.")
        string_fields = (
            self.bind_dn,
            self.base_dn,
            self.ca_certificate,
            self.client_certificate,
            self.client_key,
            self.tofu_fingerprint,
        )
        if not all(isinstance(value, str) for value in string_fields):
            raise ValueError("Profile directory settings must be strings.")
        if not isinstance(self.read_only, bool):
            raise TypeError("Read-only profile setting must be a boolean.")
        if self.authentication_mode == "simple" and not self.bind_dn.strip():
            raise ValueError("Simple authentication requires a bind DN.")
        if bool(self.client_certificate) != bool(self.client_key):
            raise ValueError("A client certificate and private key must be configured together.")
        if self.authentication_mode == "sasl_external" and not self.client_certificate:
            raise ValueError("SASL EXTERNAL requires a client certificate and private key.")
        if self.authentication_mode == "sasl_external" and self.transport == "plain":
            raise ValueError("SASL EXTERNAL requires StartTLS or LDAPS.")
        if self.tofu_fingerprint and not re.fullmatch(
            r"(?:[0-9a-fA-F]{2}:?){31}[0-9a-fA-F]{2}",
            self.tofu_fingerprint,
        ):
            raise ValueError("TOFU fingerprint must be a SHA-256 hexadecimal value.")
        try:
            parsed_profile_id = uuid.UUID(self.profile_id)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("Profile ID must be a UUID.") from error
        if str(parsed_profile_id) != self.profile_id.casefold():
            raise ValueError("Profile ID must use canonical UUID format.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DirectoryProfile:
        allowed = {field_name for field_name in cls.__dataclass_fields__}
        values = {key: value for key, value in data.items() if key in allowed}
        return cls(**values)


@dataclass(slots=True)
class Preferences:
    ai_enabled: bool = True
    require_ai_confirmation: bool = True
    model_path: str = ""
    model_sha256: str = ""
    model_provenance: str = ""
    require_delete_confirmation: bool = True
    require_rename_confirmation: bool = True
    highlight_search_attributes: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.ai_enabled, bool):
            raise TypeError("AI enabled preference must be a boolean.")
        if not isinstance(self.require_ai_confirmation, bool):
            raise TypeError("AI confirmation preference must be a boolean.")
        if not isinstance(self.highlight_search_attributes, bool):
            raise TypeError("Search highlight preference must be a boolean.")
        if not all(isinstance(value, bool) for value in (
            self.require_delete_confirmation, self.require_rename_confirmation
        )):
            raise TypeError("Directory confirmation preferences must be booleans.")
        if not all(
            isinstance(value, str)
            for value in (self.model_path, self.model_sha256, self.model_provenance)
        ):
            raise TypeError("Local model settings must be strings.")


@dataclass(slots=True)
class LocalConfiguration:
    profiles: list[DirectoryProfile] = field(default_factory=list)
    preferences: Preferences = field(default_factory=Preferences)


class ConfigurationStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or configuration_directory() / "profiles.json"

    def load(self) -> LocalConfiguration:
        if not self.path.exists():
            return LocalConfiguration()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("configuration root must be a JSON object")
            raw_profiles = raw.get("profiles", [])
            raw_preferences = raw.get("preferences", {})
            if not isinstance(raw_profiles, list) or not all(
                isinstance(item, dict) for item in raw_profiles
            ):
                raise TypeError("profiles must be a list of JSON objects")
            if not isinstance(raw_preferences, dict):
                raise TypeError("preferences must be a JSON object")
            profiles = [DirectoryProfile.from_dict(item) for item in raw_profiles]
            preferences = Preferences(**raw_preferences)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise ConfigurationError(f"Unable to load profiles from {self.path}: {error}") from error
        return LocalConfiguration(profiles, preferences)

    def save(self, configuration: LocalConfiguration) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "profiles": [asdict(profile) for profile in configuration.profiles],
            "preferences": asdict(configuration.preferences),
        }
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f"{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(document, temporary, indent=2)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            temporary_path.replace(self.path)
        except OSError as error:
            raise ConfigurationError(f"Unable to save profiles to {self.path}: {error}") from error
        finally:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink()


class CredentialVault:
    SERVICE_NAME = "DataSifter 2.0"

    def store_password(self, profile: DirectoryProfile, password: str) -> None:
        if not password:
            self.delete_password(profile)
            return
        try:
            keyring.set_password(self.SERVICE_NAME, profile.profile_id, password)
        except keyring.errors.KeyringError as error:
            raise ConfigurationError(f"OS keyring could not store the password: {error}") from error

    def get_password(self, profile: DirectoryProfile) -> str | None:
        try:
            return keyring.get_password(self.SERVICE_NAME, profile.profile_id)
        except keyring.errors.KeyringError as error:
            raise ConfigurationError(f"OS keyring could not retrieve the password: {error}") from error

    def delete_password(self, profile: DirectoryProfile) -> None:
        try:
            keyring.delete_password(self.SERVICE_NAME, profile.profile_id)
        except keyring.errors.PasswordDeleteError:
            return
        except keyring.errors.KeyringError as error:
            raise ConfigurationError(f"OS keyring could not delete the password: {error}") from error

    def store_client_key_passphrase(
        self,
        profile: DirectoryProfile,
        passphrase: str,
    ) -> None:
        self._store_secret(profile, "client-key", passphrase)

    def get_client_key_passphrase(self, profile: DirectoryProfile) -> str | None:
        try:
            return keyring.get_password(
                self.SERVICE_NAME,
                f"{profile.profile_id}:client-key",
            )
        except keyring.errors.KeyringError as error:
            raise ConfigurationError(
                f"OS keyring could not retrieve the client-key passphrase: {error}"
            ) from error

    def _store_secret(
        self,
        profile: DirectoryProfile,
        secret_name: str,
        secret: str,
    ) -> None:
        username = f"{profile.profile_id}:{secret_name}"
        try:
            if secret:
                keyring.set_password(self.SERVICE_NAME, username, secret)
            else:
                keyring.delete_password(self.SERVICE_NAME, username)
        except keyring.errors.PasswordDeleteError:
            return
        except keyring.errors.KeyringError as error:
            raise ConfigurationError(f"OS keyring could not store {secret_name}: {error}") from error
