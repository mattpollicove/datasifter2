"""Certificate details and profile-scoped TOFU pin storage."""

from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from datasifter.config import DirectoryProfile, configuration_directory


class CertificateTrustError(RuntimeError):
    """Raised when a directory certificate cannot be safely trusted."""


@dataclass(frozen=True, slots=True)
class CertificateDetails:
    fingerprint_sha256: str
    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime


def certificate_details(certificate_pem: bytes) -> CertificateDetails:
    try:
        certificate = x509.load_pem_x509_certificate(certificate_pem)
    except ValueError as error:
        raise CertificateTrustError(f"Unable to parse the server certificate: {error}") from error
    return CertificateDetails(
        fingerprint_sha256=certificate.fingerprint(hashes.SHA256()).hex(":").upper(),
        subject=certificate.subject.rfc4514_string(),
        issuer=certificate.issuer.rfc4514_string(),
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
    )


class CertificateTrustStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or configuration_directory() / "certificates"

    def certificate_path(self, profile: DirectoryProfile) -> Path:
        return self.directory / f"{profile.profile_id}.pem"

    def accept_tofu(self, profile: DirectoryProfile, certificate_pem: bytes) -> CertificateDetails:
        details = certificate_details(certificate_pem)
        if profile.tofu_fingerprint:
            raise CertificateTrustError("A trusted certificate already exists for this profile.")
        self._write_certificate(profile, certificate_pem)
        profile.tofu_fingerprint = details.fingerprint_sha256
        return details

    def replace_tofu(
        self,
        profile: DirectoryProfile,
        certificate_pem: bytes,
        expected_fingerprint: str,
    ) -> CertificateDetails:
        details = certificate_details(certificate_pem)
        if not profile.tofu_fingerprint or not hmac.compare_digest(
            profile.tofu_fingerprint.casefold(),
            expected_fingerprint.casefold(),
        ):
            raise CertificateTrustError("The profile trust changed before it could be replaced.")
        self._write_certificate(profile, certificate_pem)
        profile.tofu_fingerprint = details.fingerprint_sha256
        return details

    def _write_certificate(self, profile: DirectoryProfile, certificate_pem: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.certificate_path(profile)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.directory,
                prefix=f"{profile.profile_id}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(certificate_pem)
                temporary.flush()
                os.fsync(temporary.fileno())
            temporary_path.replace(destination)
        except OSError as error:
            raise CertificateTrustError(f"Unable to save the trusted certificate: {error}") from error
        finally:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink()

    def verify_fingerprint(self, profile: DirectoryProfile, certificate_der: bytes) -> None:
        if not profile.tofu_fingerprint:
            return
        actual = hashlib.sha256(certificate_der).hexdigest()
        expected = profile.tofu_fingerprint.replace(":", "").casefold()
        if not hmac.compare_digest(actual, expected):
            raise CertificateTrustError(
                "The directory certificate fingerprint changed; connection was stopped."
            )

    def pem_for_profile(self, profile: DirectoryProfile) -> str | None:
        if not profile.tofu_fingerprint:
            return None
        path = self.certificate_path(profile)
        if not path.is_file():
            raise CertificateTrustError(
                "The profile has a TOFU fingerprint but its trusted certificate file is missing."
            )
        return str(path)
