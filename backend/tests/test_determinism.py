"""Regression tests for repeatable local-demo identifiers."""

from __future__ import annotations

import hashlib

from backend.app.main import _stable_mod as main_stable_mod
from backend.app.tools.mock_sandbox import _stable_mod as sandbox_stable_mod


def _expected(value: str, modulus: int) -> int:
    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:8], "big") % modulus


def test_demo_identifier_derivation_is_not_affected_by_python_hash_seed() -> None:
    value = "Bangalore:session-42"
    modulus = 900_000
    expected = _expected(value, modulus)

    assert main_stable_mod(value, modulus) == expected
    assert sandbox_stable_mod(value, modulus) == expected
