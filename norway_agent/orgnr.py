"""Norwegian organisation number helpers (9 digits, MOD-11 check digit)."""

from __future__ import annotations

import re

_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)


def normalize(raw: object) -> str | None:
    """'NO 923 609 016 MVA' -> '923609016'. None if it is not 9 digits."""
    digits = re.sub(r"\D", "", str(raw))
    return digits if len(digits) == 9 else None


def check_digit(first8: str) -> int | None:
    total = sum(int(d) * w for d, w in zip(first8, _WEIGHTS))
    r = 11 - total % 11
    if r == 11:
        return 0
    if r == 10:
        return None  # no valid check digit exists for this prefix
    return r


def is_valid(orgnr: str) -> bool:
    if len(orgnr) != 9 or not orgnr.isdigit():
        return False
    return check_digit(orgnr[:8]) == int(orgnr[8])
