from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from datasifter.config import DirectoryProfile
from datasifter.security.certificates import CertificateTrustError, CertificateTrustStore


def make_certificate(common_name: str) -> tuple[bytes, bytes]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM),
        certificate.public_bytes(serialization.Encoding.DER),
    )


def test_accept_tofu_persists_certificate_and_pin(tmp_path):
    profile = DirectoryProfile(name="test", host="ldap.test")
    certificate_pem, certificate_der = make_certificate("ldap.test")
    store = CertificateTrustStore(tmp_path)

    details = store.accept_tofu(profile, certificate_pem)
    store.verify_fingerprint(profile, certificate_der)

    assert details.fingerprint_sha256 == profile.tofu_fingerprint
    assert store.certificate_path(profile).read_bytes() == certificate_pem


def test_tofu_rotation_is_rejected(tmp_path):
    profile = DirectoryProfile(name="test", host="ldap.test")
    original, _ = make_certificate("ldap.test")
    replacement, replacement_der = make_certificate("ldap.test")
    store = CertificateTrustStore(tmp_path)
    original_details = store.accept_tofu(profile, original)

    with pytest.raises(CertificateTrustError, match="already exists"):
        store.accept_tofu(profile, replacement)

    replacement_details = store.replace_tofu(
        profile,
        replacement,
        original_details.fingerprint_sha256,
    )
    store.verify_fingerprint(profile, replacement_der)

    assert replacement_details.fingerprint_sha256 != original_details.fingerprint_sha256
