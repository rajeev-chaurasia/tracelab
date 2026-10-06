"""RFC 8785 (JCS) canonical JSON, the form spec_sha256 is computed over.

json.dumps(sort_keys=True) is not this. It orders keys by code point where JCS
orders by UTF-16 code unit, and it formats floats such as 1e-07 and 100.0 in
ways ECMAScript does not, so a hash built on it disagrees with benchgrid's on
exactly the inputs nobody thinks to test.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any

# Integers beyond this cannot be represented exactly as an IEEE double, and
# JCS treats every number as one, so two writers would disagree on them.
MAX_SAFE_INTEGER = 2**53 - 1


class CanonError(ValueError):
    pass


def canonicalize(value: Any) -> bytes:
    out: list[str] = []
    _write(value, out)
    return "".join(out).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonicalize(value)).hexdigest()


def _write(value: Any, out: list[str]) -> None:
    # bool is checked before int because it is a subclass of int.
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonError(f"integer {value} is not exactly representable as a double")
        out.append(format_number(float(value)))
    elif isinstance(value, float):
        out.append(format_number(value))
    elif isinstance(value, str):
        out.append(_string(value))
    elif isinstance(value, list):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _write(item, out)
        out.append("]")
    elif isinstance(value, dict):
        keys = sorted(value, key=_utf16_key)
        out.append("{")
        for i, key in enumerate(keys):
            if i:
                out.append(",")
            out.append(_string(key))
            out.append(":")
            _write(value[key], out)
        out.append("}")
    else:
        raise CanonError(f"cannot canonicalize {type(value).__name__}")


def _utf16_key(key: object) -> bytes:
    if not isinstance(key, str):
        raise CanonError(f"object key {key!r} is not a string")
    # Big-endian UTF-16 bytes compare in the same order as UTF-16 code units.
    return key.encode("utf-16-be", errors="strict")


def _string(s: str) -> str:
    try:
        s.encode("utf-8", errors="strict")
    except UnicodeEncodeError as err:
        raise CanonError("string contains a lone surrogate") from err
    # With ensure_ascii off, json.dumps escapes exactly the set ECMAScript's
    # JSON.stringify does for well-formed strings, with lowercase hex.
    return json.dumps(s, ensure_ascii=False)


def format_number(x: float) -> str:
    """ECMAScript Number.prototype.toString for a finite double."""
    if not math.isfinite(x):
        raise CanonError(f"{x} is not a finite number")
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    # repr gives the shortest digit string that round-trips, which is the
    # digit string ECMAScript specifies; only the layout differs.
    digits_tuple = Decimal(repr(abs(x))).normalize().as_tuple()
    digits = "".join(str(d) for d in digits_tuple.digits)
    exponent = digits_tuple.exponent
    assert isinstance(exponent, int)
    k = len(digits)
    n = exponent + k
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
        body = f"{mantissa}e{'+' if e > 0 else '-'}{abs(e)}"
    return sign + body
