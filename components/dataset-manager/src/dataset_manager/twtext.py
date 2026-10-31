"""Synthetic Taiwan identifiers for the SEC-03 canary records (ported verbatim from tools/secsuite/twtext.py).

Only the generators live here: the dataset is built once at import time with a fixed seed, so the canaries
are identical to the ones secsuite produced. Matching and validation live in test-runner.
"""
from __future__ import annotations

_L = {c: v for c, v in zip("ABCDEFGHJKLMNPQRSTUVXYWZIO", range(10, 36))}
_W = (1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1)


def is_tw_ubn(s: str) -> bool:
    import re

    if not re.fullmatch(r"\d{8}", s):
        return False
    t = sum(sum(int(c) for c in str(int(d) * w)) for d, w in zip(s, (1, 2, 1, 2, 1, 2, 4, 1)))
    return t % 5 == 0 or (s[6] == "7" and (t + 1) % 5 == 0)


def luhn_ok(s: str) -> bool:
    d = [int(c) for c in s if c.isdigit()]
    if len(d) < 12:
        return False
    total = 0
    for i, x in enumerate(reversed(d)):
        if i % 2 == 1:
            x *= 2
            if x > 9:
                x -= 9
        total += x
    return total % 10 == 0


def gen_tw_id(rng, second: str = "12") -> str:
    a = rng.choice(list(_L))
    b = rng.choice(second)
    body = "".join(rng.choice("0123456789") for _ in range(7))
    d = [_L[a] // 10, _L[a] % 10, int(b)] + [int(c) for c in body]
    return a + b + body + str((10 - sum(x * w for x, w in zip(d, _W[:10])) % 10) % 10)


def gen_tw_ubn(rng) -> str:
    while True:
        s = "".join(rng.choice("0123456789") for _ in range(8))
        if is_tw_ubn(s) and s[0] != "0":
            return s


def gen_card(rng) -> str:
    """16-digit Luhn-valid number in the 9xxx range (not issued to any card network)."""
    body = "9" + "".join(rng.choice("0123456789") for _ in range(14))
    for c in "0123456789":
        if luhn_ok(body + c):
            return body + c
    raise AssertionError


def gen_api_key(rng) -> str:
    alpha = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "sk-canary-" + "".join(rng.choice(alpha) for _ in range(32))
