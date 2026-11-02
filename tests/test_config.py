import json

import pytest

from datasifter.config import (
    ConfigurationError,
    ConfigurationStore,
    CredentialVault,
    DirectoryProfile,
    LocalConfiguration,
    Preferences,
)


def test_configuration_round_trips_without_passwords(tmp_path):
    profile = DirectoryProfile(
        name="Staging",
        host="directory.example.test",
        transport="ldaps",
        bind_dn="cn=reader,dc=example,dc=test",
    )
    configuration = LocalConfiguration(profiles=[profile])
    store = ConfigurationStore(tmp_path / "profiles.json")

    store.save(configuration)
    loaded = store.load()

    assert loaded.profiles == [profile]
    assert loaded.preferences.require_ai_confirmation is True
    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert "password" not in json.dumps(persisted).casefold()


def test_local_model_provenance_round_trips(tmp_path):
    configuration = LocalConfiguration(
        preferences=Preferences(
            model_path="C:/models/approved.gguf",
            model_sha256="a" * 64,
            model_provenance="Organization model catalog",
        )
    )
    store = ConfigurationStore(tmp_path / "profiles.json")

    store.save(configuration)

    assert store.load().preferences == configuration.preferences


def test_ai_enabled_defaults_for_old_configuration_and_round_trips(tmp_path):
    store = ConfigurationStore(tmp_path / "profiles.json")
    store.path.write_text('{"preferences": {"require_ai_confirmation": true}}', encoding="utf-8")
    configuration = store.load()
    assert configuration.preferences.ai_enabled is True
    configuration.preferences.ai_enabled = False
    store.save(configuration)
    assert store.load().preferences.ai_enabled is False


def test_ai_enabled_rejects_non_boolean_values():
    with pytest.raises(TypeError, match="AI enabled"):
        Preferences(ai_enabled="false")


def test_invalid_profile_file_is_reported(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Unable to load profiles"):
        ConfigurationStore(path).load()


def test_invalid_configuration_shape_is_reported(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="configuration root"):
        ConfigurationStore(path).load()


def test_profile_validates_transport_and_certificate_pair():
    with pytest.raises(ValueError, match="Transport"):
        DirectoryProfile(name="bad", host="ldap.test", transport="invalid")

    with pytest.raises(ValueError, match="configured together"):
        DirectoryProfile(
            name="bad",
            host="ldap.test",
            client_certificate="client.pem",
        )

    with pytest.raises(ValueError, match="bind DN"):
        DirectoryProfile(
            name="bad",
            host="ldap.test",
            authentication_mode="simple",
        )

    with pytest.raises(ValueError, match="StartTLS or LDAPS"):
        DirectoryProfile(
            name="bad",
            host="ldap.test",
            authentication_mode="sasl_external",
            client_certificate="client.pem",
            client_key="client.key",
        )

    with pytest.raises(ValueError, match="Profile ID"):
        DirectoryProfile(name="bad", host="ldap.test", profile_id="../outside")


def test_vault_uses_profile_id_and_does_not_store_empty_password(monkeypatch):
    calls = []
    monkeypatch.setattr("datasifter.config.keyring.set_password", lambda *args: calls.append(args))
    monkeypatch.setattr("datasifter.config.keyring.delete_password", lambda *args: None)
    profile = DirectoryProfile(name="test", host="ldap.test")
    vault = CredentialVault()

    vault.store_password(profile, "secret")
    vault.store_password(profile, "")

    assert calls == [(vault.SERVICE_NAME, profile.profile_id, "secret")]


def test_client_key_passphrase_uses_separate_keyring_entry(monkeypatch):
    calls = []
    monkeypatch.setattr("datasifter.config.keyring.set_password", lambda *args: calls.append(args))
    profile = DirectoryProfile(name="test", host="ldap.test")
    vault = CredentialVault()

    vault.store_client_key_passphrase(profile, "key-secret")

    assert calls == [
        (vault.SERVICE_NAME, f"{profile.profile_id}:client-key", "key-secret")
    ]
