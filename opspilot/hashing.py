"""Stable hashes for text artifacts across Git checkout line endings."""

from __future__ import annotations

import hashlib
from pathlib import Path


def canonical_text_bytes(payload: bytes) -> bytes:
    """Return Git-style LF bytes without changing the source file."""
    return payload.replace(b"\r\n", b"\n")


def sha256_text_bytes(payload: bytes) -> str:
    return hashlib.sha256(canonical_text_bytes(payload)).hexdigest()


def sha256_text_file(path: Path) -> str:
    return sha256_text_bytes(path.read_bytes())


def verify_text_sha256(path: Path, expected: str) -> str:
    """Verify a frozen digest while accepting LF or CRLF checkout bytes."""
    payload = path.read_bytes()
    lf_bytes = canonical_text_bytes(payload)
    crlf_bytes = lf_bytes.replace(b"\n", b"\r\n")
    if expected not in {
        hashlib.sha256(payload).hexdigest(),
        hashlib.sha256(lf_bytes).hexdigest(),
        hashlib.sha256(crlf_bytes).hexdigest(),
    }:
        raise ValueError("text artifact hash does not match frozen source hash")
    return expected
