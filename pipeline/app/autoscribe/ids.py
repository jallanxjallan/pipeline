from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars = ["0"] * length
    for i in range(length - 1, -1, -1):
        chars[i] = _ALPHABET[value & 31]
        value >>= 5
    return "".join(chars)


def new_ulid() -> str:
    """Generate a 26-character Crockford-base32 ULID without dependencies."""
    timestamp_ms = int(time.time_ns() // 1_000_000)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode(timestamp_ms, 10) + _encode(randomness, 16)
