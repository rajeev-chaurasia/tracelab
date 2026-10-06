import json

import pytest

from tracelab.core.canon import CanonError, canonicalize, format_number

# Non-ASCII is written as escapes throughout. Editors and some tooling apply
# NFC on save, and U+FB33 has a canonical decomposition, so a literal one can
# silently become two code points and change the bytes under test.


def test_rfc8785_section_3_2_2() -> None:
    source = r"""{
      "numbers": [333333333.33333329, 1E30, 4.50, 2e-3, 0.000000000000000000000000001],
      "string": "\u20ac$\u000F\u000aA'\u0042\u0022\u005c\\\"\/",
      "literals": [null, true, false]
    }"""
    expected = (
        '{"literals":[null,true,false],'
        '"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"\u20ac' + r"$\u000f\nA'B\"\\\\\"/" + '"}'
    )

    assert canonicalize(json.loads(source)) == expected.encode()


def test_rfc8785_section_3_2_3_sorts_by_utf16_code_unit() -> None:
    keys = ["\u20ac", "\r", "\ufb33", "1", "\U0001f600", "\u0080", "\u00f6"]

    out = json.loads(canonicalize(dict.fromkeys(keys, 0)))

    # U+1F600 sorts before U+FB33 because its first UTF-16 code unit is 0xD83D.
    # Sorting by code point, as Python does, gets these two the other way round.
    assert list(out) == ["\r", "1", "\u0080", "\u00f6", "\u20ac", "\U0001f600", "\ufb33"]
    assert sorted(keys) != list(out)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (100.0, "100"),
        (1e-07, "1e-7"),
        (0.000001, "0.000001"),
        (1e21, "1e+21"),
        (1e20, "100000000000000000000"),
        (-0.0, "0"),
        (5e-324, "5e-324"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
        (-2.5e-7, "-2.5e-7"),
        (0.1, "0.1"),
        (123.456, "123.456"),
    ],
)
def test_numbers_use_ecmascript_layout(value: float, expected: str) -> None:
    assert format_number(value) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 2**53, "\ud800", {1: 2}])
def test_rejects_what_two_writers_could_not_agree_on(value: object) -> None:
    with pytest.raises(CanonError):
        canonicalize(value)
