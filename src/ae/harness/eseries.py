"""IEC 60063 preferred-value series for the patch validator (E12/E24/E96). Why: improve-live-6 was accepted with R1 = 59.6k, which is NOT an E96 value although the manifest declares the 1% resistor family
as series E96; such a part is not expected to be orderable. When a policy sets allowed_edits.enforce_series, every edited `value` must be a member of the part's declared series."""
from __future__ import annotations

import math

from ae.spice import parse_si

E12 = (10, 12, 15, 18, 22, 27, 33, 39, 47, 56, 68, 82)
E24 = (10, 11, 12, 13, 15, 16, 18, 20, 22, 24, 27, 30, 33, 36, 39, 43, 47, 51, 56, 62, 68, 75, 82, 91)
E96 = (100, 102, 105, 107, 110, 113, 115, 118, 121, 124, 127, 130, 133, 137, 140, 143, 147, 150, 154, 158, 162, 165, 169, 174, 178, 182, 187, 191, 196,
       200, 205, 210, 215, 221, 226, 232, 237, 243, 249, 255, 261, 267, 274, 280, 287, 294, 301, 309, 316, 324, 332, 340, 348, 357, 365, 374, 383, 392,
       402, 412, 422, 432, 442, 453, 464, 475, 487, 499, 511, 523, 536, 549, 562, 576, 590, 604, 619, 634, 649, 665, 681, 698, 715, 732, 750, 768, 787, 806, 825, 845, 866, 887, 909, 931, 953, 976)
SERIES = {"E12": E12, "E24": E24, "E96": E96}


def mantissa_int(value: float) -> int:
    """Value scaled into [1, 10) as a 3-digit integer (59000 -> 590, 5.9e-9 -> 590, 59600 -> 596)."""
    exp = math.floor(math.log10(value) + 1e-12)
    return round(value / 10 ** exp * 100)


def in_series(value_text: str, series: str) -> bool:
    if series not in SERIES:
        raise ValueError(f"unsupported series {series!r} (supported: {sorted(SERIES)})")
    m = mantissa_int(parse_si(value_text))                         # 3-digit integer in [100, 999]
    if series == "E96":
        return m in E96
    return m % 10 == 0 and m // 10 in SERIES[series]              # E12/E24 hold two-digit mantissas: 10.4n or 10.6n must NOT snap onto 10n or 11n
