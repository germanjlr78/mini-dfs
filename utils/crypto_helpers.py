import hashlib


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 digest for a block."""
    return hashlib.sha256(data).hexdigest()


def verify_sha256(data: bytes, expected_checksum: str) -> bool:
    """Compare a block's SHA-256 digest with its stored checksum."""
    return sha256_bytes(data) == expected_checksum