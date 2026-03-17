"""
tests/test_utils.py
-------------------
Unit tests for utils.py
"""

import sys
import os

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils import (
    to_ts,
    to_pct,
    sfloat,
    as_code,
    nonempty,
    safe_replace_year,
    add_years,
    fmt_date,
    pick_first,
    merge_state,
)


# ---------------------------------------------------------------------------
# to_ts
# ---------------------------------------------------------------------------

def test_to_ts_normalizes_alternate_date_format():
    assert to_ts("12/31/2026") == pd.Timestamp("2026-12-31")

def test_to_ts_normalizes_alternate_date_format2():
    assert to_ts("2026/01/15") == pd.Timestamp("2026-01-15")

def test_to_ts_already_timestamp():
    ts = pd.Timestamp("2026-01-15")
    assert to_ts(ts) is ts

def test_to_ts_none_returns_nat():
    assert pd.isna(to_ts(None))

def test_to_ts_invalid_string_returns_nat():
    assert pd.isna(to_ts("not-a-date"))

def test_to_ts_empty_string_returns_nat():
    assert pd.isna(to_ts(""))


# ---------------------------------------------------------------------------
# to_pct
# ---------------------------------------------------------------------------
def test_to_pct_percent_string():
    assert to_pct("5.75%") == pytest.approx(0.0575)

def test_to_pct_numeric_greater_than_one():
    assert to_pct(5.75) == pytest.approx(0.0575)

def test_to_pct_numeric_less_than_one():
    # abs(0.5) is not > 1, so returned as-is
    assert to_pct(0.5) == pytest.approx(0.5)

def test_to_pct_small_decimal_unchanged():
    assert to_pct(0.0575) == pytest.approx(0.0575)

def test_to_pct_none_returns_none():
    assert to_pct(None) is None

def test_to_pct_empty_string_returns_none():
    assert to_pct("  ") is None

def test_to_pct_thousands_comma_stripped():
    # Comma is a thousands separator: "5,75%" → "575%" → 5.75 (not 0.0575)
    assert to_pct("5,75%") == pytest.approx(5.75)


# ---------------------------------------------------------------------------
# sfloat
# ---------------------------------------------------------------------------

def test_sfloat_valid_string():
    assert sfloat("1234.56") == 1234.56

def test_sfloat_none_returns_default():
    assert sfloat(None, 99.0) == 99.0

def test_sfloat_unconvertible_returns_default():
    assert sfloat("abc", -1.0) == -1.0

def test_sfloat_zero_is_valid():
    assert sfloat(0) == 0.0

def test_sfloat_integer_input():
    assert sfloat(42) == 42.0


# ---------------------------------------------------------------------------
# as_code
# ---------------------------------------------------------------------------

def test_as_code_float_whole_number():
    assert as_code(5.0) == "5"

def test_as_code_integer():
    assert as_code(10) == "10"

def test_as_code_string_stripped():
    assert as_code("  7 ") == "7"

def test_as_code_none_returns_empty():
    assert as_code(None) == ""


# ---------------------------------------------------------------------------
# nonempty
# ---------------------------------------------------------------------------

def test_nonempty_empty_string():
    assert nonempty("") is False

def test_nonempty_whitespace_only():
    assert nonempty("   ") is False

def test_nonempty_none():
    assert nonempty(None) is False

def test_nonempty_nan():
    assert nonempty(float("nan")) is False

def test_nonempty_zero_is_truthy():
    assert nonempty(0) is True

def test_nonempty_false_is_truthy():
    assert nonempty(False) is True

def test_nonempty_valid_string():
    assert nonempty("hello") is True

def test_nonempty_valid_number():
    assert nonempty(3.14) is True


# ---------------------------------------------------------------------------
# safe_replace_year
# ---------------------------------------------------------------------------

def test_safe_replace_year_normal_date():
    result = safe_replace_year(pd.Timestamp("2026-06-15"), 2030)
    assert result == pd.Timestamp("2030-06-15")

def test_safe_replace_year_leap_day_to_non_leap_year():
    result = safe_replace_year(pd.Timestamp("2024-02-29"), 2025)
    assert result == pd.Timestamp("2025-02-28")

def test_safe_replace_year_nat_returns_nat():
    assert pd.isna(safe_replace_year(pd.NaT, 2026))

# ---------------------------------------------------------------------------
# add_years
# ---------------------------------------------------------------------------

def test_add_years_basic():
    assert add_years("2026-01-15", 5) == pd.Timestamp("2031-01-15")

def test_add_years_zero():
    assert add_years("2026-01-15", 0) == pd.Timestamp("2026-01-15")

def test_add_years_leap_day():
    assert add_years("2024-02-29", 1) == pd.Timestamp("2025-02-28")

def test_add_years_nat_input():
    assert pd.isna(add_years(pd.NaT, 3))


# ---------------------------------------------------------------------------
# fmt_date
# ---------------------------------------------------------------------------

def test_fmt_date_timestamp():
    assert fmt_date(pd.Timestamp("2026-01-15")) == "2026-01-15"

def test_fmt_date_string_input():
    assert fmt_date("2026-01-15") == "2026-01-15"

def test_fmt_date_none_returns_blank():
    assert fmt_date(None) == ""

def test_fmt_date_nat_returns_blank():
    assert fmt_date(pd.NaT) == ""


# ---------------------------------------------------------------------------
# pick_first
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_row():
    return pd.Series({
        "ColA": None,
        "ColB": "",
        "ColC": "found",
        "ColD": "also here",
    })

def test_pick_first_skips_none_and_blank(sample_row):
    assert pick_first(sample_row, "ColA", "ColB", "ColC") == "found"

def test_pick_first_returns_first_match(sample_row):
    assert pick_first(sample_row, "ColC", "ColD") == "found"

def test_pick_first_missing_column_skipped(sample_row):
    assert pick_first(sample_row, "NoSuchCol", "ColC") == "found"

def test_pick_first_all_empty_returns_none(sample_row):
    assert pick_first(sample_row, "ColA", "ColB") is None


# ---------------------------------------------------------------------------
# merge_state
# ---------------------------------------------------------------------------

def test_merge_state_basic_merge():
    result = merge_state({"B": 2}, base={"A": 1})
    assert result == {"A": 1, "B": 2}

def test_merge_state_overwrites():
    result = merge_state({"A": 1}, {"A": 99})
    assert result["A"] == 99

def test_merge_state_none_values_not_applied():
    result = merge_state({"A": None}, base={"A": "original"})
    assert result["A"] == "original"

def test_merge_state_extras_applied():
    result = merge_state({"A": 1}, base={"A": 0}, extras={"A": 999})
    assert result["A"] == 999

def test_merge_state_base_not_mutated(): # Did calling this function accidentally modify the input dictionary?
    base = {"A": 1}
    merge_state({"B": 2}, base=base)
    assert "B" not in base
