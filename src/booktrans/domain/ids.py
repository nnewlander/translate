from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import secrets


def new_run_id(source_path: Path) -> str:
    """A traceable run id without embedding source content in the path."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    discriminator = sha256(str(source_path.resolve()).encode()).hexdigest()[:6]
    return f"inspect-{timestamp}-{discriminator}-{secrets.token_hex(3)}"


def new_extract_run_id(source_path: Path) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    discriminator = sha256(str(source_path.resolve()).encode()).hexdigest()[:6]
    return f"extract-{timestamp}-{discriminator}-{secrets.token_hex(3)}"


def new_identity_run_id(source_path: Path) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    discriminator = sha256(str(source_path.resolve()).encode()).hexdigest()[:6]
    return f"identity-{timestamp}-{discriminator}-{secrets.token_hex(3)}"


def new_synthetic_run_id(source_path: Path) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    discriminator = sha256(str(source_path.resolve()).encode()).hexdigest()[:6]
    return f"synthetic-{timestamp}-{discriminator}-{secrets.token_hex(3)}"


def new_format_audit_run_id(source_path: Path) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    discriminator = sha256(str(source_path.resolve()).encode()).hexdigest()[:6]
    return f"format-audit-{timestamp}-{discriminator}-{secrets.token_hex(3)}"


def new_format_fragment_audit_run_id(source_path: Path) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    discriminator = sha256(str(source_path.resolve()).encode()).hexdigest()[:6]
    return f"format-fragment-audit-{timestamp}-{discriminator}-{secrets.token_hex(3)}"


def new_tagged_synthetic_run_id(source_path: Path) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    discriminator = sha256(str(source_path.resolve()).encode()).hexdigest()[:6]
    return f"tagged-synthetic-{timestamp}-{discriminator}-{secrets.token_hex(3)}"


def new_structural_tagged_synthetic_run_id(source_path: Path) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    discriminator = sha256(str(source_path.resolve()).encode()).hexdigest()[:6]
    return f"structural-tagged-synthetic-{timestamp}-{discriminator}-{secrets.token_hex(3)}"


def new_calibration_run_id(source_path: Path) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    discriminator = sha256(str(source_path.resolve()).encode()).hexdigest()[:6]
    return f"calibration-{timestamp}-{discriminator}-{secrets.token_hex(3)}"


def stable_uid(*components: str) -> str:
    """Deterministic identity for immutable source structure, never a run UUID."""
    material = "\x1f".join(components).encode("utf-8")
    return sha256(material).hexdigest()
