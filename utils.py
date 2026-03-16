"""
utils.py
--------
Pure helper functions for the MYGA/FIA actuarial engine.

All functions here are stateless and have no side effects.
They depend only on their arguments (and pandas/stdlib).
Nothing in this module imports from config, models, or the events package —
it is safe to import from anywhere.

Contents
--------
  Type / value coercion
    to_ts        - convert to pd.Timestamp (NaT on failure)
    to_pct       - convert % string or numeric to decimal rate
    sfloat       - safe float with configurable default
    as_code      - normalise a product / plan code to a clean string

  Predicates
    nonempty     - True if a value is meaningfully filled in

  Date arithmetic
    safe_replace_year  - replace year on a Timestamp, handling Feb-29
    add_years          - add N years to a Timestamp

  Formatting
    fmt_date     - format a date as YYYY-MM-DD string (or blank)
    fmt_output   - format any value for Excel output

  Dict utilities
    pick_first   - return first non-empty value from a list of column names
    merge_state  - build a state dict by layering multiple blocks
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from config import RATE_FIELDS


# ---------------------------------------------------------------------------
# Type / value coercion
# ---------------------------------------------------------------------------

def to_ts(x: Any) -> pd.Timestamp:
    """
    Convert *x* to a :class:`pd.Timestamp`.

    Returns ``pd.NaT`` on any failure rather than raising.

    Examples
    --------
    >>> to_ts("2026-01-15")
    Timestamp('2026-01-15 00:00:00')
    >>> to_ts(None)
    NaT
    """
    if isinstance(x, pd.Timestamp):
        return x
    return pd.to_datetime(x, errors="coerce")


def to_pct(x: Any) -> Optional[float]:
    """
    Convert a rate or percentage into decimal form.

    Rules
    -----
    - ``"5.75%"``  → ``0.0575``
    - ``5.75``     → ``0.0575``   (values > 1 are divided by 100)
    - ``0.0575``   → ``0.0575``   (values already ≤ 1 are returned as-is)
    - ``None`` / ``NaN`` / ``""`` → ``None``

    Examples
    --------
    >>> to_pct("5.75%")
    0.0575
    >>> to_pct(5.75)
    0.0575
    >>> to_pct(0.0575)
    0.0575
    """
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    if isinstance(x, str):
        x = x.strip().replace(",", "")
        if x == "":
            return None
        if x.endswith("%"):
            return float(x[:-1]) / 100
        x = float(x)
    else:
        x = float(x)
    return x / 100 if abs(x) > 1 else x


def sfloat(x: Any, default: float = 0.0) -> float:
    """
    Safely cast *x* to float, returning *default* on any failure.

    Treats ``None``, ``NaN``, and unconvertible values as missing.

    Examples
    --------
    >>> sfloat("1234.56")
    1234.56
    >>> sfloat(None, default=0.0)
    0.0
    """
    if x is None:
        return default
    try:
        v = float(x)
        return default if pd.isna(v) else v
    except Exception:
        return default


def as_code(x: Any) -> str:
    """
    Normalise a product code or plan code to a clean string.

    Numeric floats that represent whole numbers are returned without a
    decimal point: ``5.0`` becomes ``"5"``, not ``"5.0"``.

    Examples
    --------
    >>> as_code(5.0)
    '5'
    >>> as_code("  10 ")
    '10'
    """
    if not nonempty(x):
        return ""
    if isinstance(x, (int, float)) and float(x).is_integer():
        return str(int(x))
    return str(x).strip()


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------

def nonempty(x: Any) -> bool:
    """
    Return ``True`` if *x* is meaningfully filled in.

    Treats ``None``, ``NaN``, blank strings, and the string ``"nan"``
    as empty.

    Examples
    --------
    >>> nonempty("")
    False
    >>> nonempty(0.0)
    True
    >>> nonempty(float("nan"))
    False
    """
    if x is None:
        return False
    if isinstance(x, float) and pd.isna(x):
        return False
    return str(x).strip() not in ("", "nan")


# ---------------------------------------------------------------------------
# Date arithmetic
# ---------------------------------------------------------------------------

def safe_replace_year(ts: Any, year: int) -> pd.Timestamp:
    """
    Replace the year component of *ts* with *year*.

    Handles Feb-29 leap-year dates gracefully: if the exact date does not
    exist in the target year, Feb-28 is used instead.

    Returns ``pd.NaT`` if *ts* cannot be parsed.
    """
    ts = to_ts(ts)
    if pd.isna(ts):
        return pd.NaT
    try:
        return ts.replace(year=year)
    except ValueError:
        return ts.replace(year=year, day=28)


def add_years(ts: Any, years: int) -> pd.Timestamp:
    """
    Add *years* to *ts*, delegating Feb-29 edge cases to
    :func:`safe_replace_year`.

    Returns ``pd.NaT`` if *ts* cannot be parsed.
    """
    ts = to_ts(ts)
    if pd.isna(ts):
        return pd.NaT
    return safe_replace_year(ts, ts.year + years)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt_date(x: Any) -> str:
    """
    Format a date value as ``YYYY-MM-DD``.

    Returns ``""`` for invalid / missing dates.

    Examples
    --------
    >>> fmt_date(pd.Timestamp("2026-02-01"))
    '2026-02-01'
    >>> fmt_date(None)
    ''
    """
    ts = to_ts(x)
    return "" if pd.isna(ts) else ts.strftime("%Y-%m-%d")


def fmt_output(v: Any, field: Optional[str] = None) -> Any:
    """
    Format a value for writing to the final Excel output.

    - Dates      → ``YYYY-MM-DD`` string
    - Rate fields → ``"5.7500%"`` string (4 decimal places)
    - None / NaN → ``""``
    - Everything else → returned unchanged

    *field* must be the column name so that rate-field detection works.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, float) and field in RATE_FIELDS:
        return f"{v:.4%}"
    return v


# ---------------------------------------------------------------------------
# Dict utilities
# ---------------------------------------------------------------------------

def pick_first(row: "pd.Series", *names: str) -> Any:
    """
    Return the first non-empty value found in *row* at any of *names*.

    Useful when the input Excel file may use slightly different column
    headers across versions (e.g. ``"Valuation Date"`` vs
    ``"ValuationDate"``).

    Returns ``None`` if no matching non-empty column is found.
    """
    for name in names:
        if name in row.index and nonempty(row[name]):
            return row[name]
    return None


def merge_state(
    *blocks: Optional[Dict[str, Any]],
    base: Optional[Dict[str, Any]] = None,
    extras: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build a state dictionary by layering multiple blocks on top of *base*.

    Blocks are applied left-to-right; later blocks overwrite earlier ones.
    Keys with ``None`` values in a block are **not** applied (they do not
    overwrite a non-None value from an earlier block).
    *extras* are always applied last and can overwrite everything.

    Parameters
    ----------
    *blocks :
        Dicts to layer, in order.
    base :
        Starting state dict (copied so the original is not mutated).
    extras :
        Final overrides applied after all blocks.

    Returns
    -------
    dict
        The merged state.
    """
    state: Dict[str, Any] = {} if base is None else base.copy()
    for block in blocks:
        if block:
            state.update({k: v for k, v in block.items() if v is not None})
    if extras:
        state.update(extras)
    return state
