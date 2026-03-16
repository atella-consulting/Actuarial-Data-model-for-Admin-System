"""
calculations.py
---------------
Pure actuarial and financial calculation functions for the MYGA/FIA engine.

Every function here is deterministic and side-effect-free.
They take plain Python / pandas values and return plain values;
none of them read global state or write to files.

Contents
--------
  policy_year            - derive the current policy year number
  month_diff             - whole-month count between two dates
  sc_rate                - look up surrender charge rate for a policy year
  snapshot               - build the standard set of derived balance fields
  maturity_date_from_...  - compute maturity date from issue date and DOB
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from utils import to_ts, sfloat, safe_replace_year


# ---------------------------------------------------------------------------
# Policy year
# ---------------------------------------------------------------------------

def policy_year(issue: Any, val: Any) -> int:
    """
    Return the policy year number (1-based) for a given valuation date.

    The policy year increments on each anniversary of the issue date.
    If either date is missing, year 1 is assumed.

    Parameters
    ----------
    issue :
        Policy issue date.
    val :
        Valuation date being assessed.

    Returns
    -------
    int
        Policy year, always ≥ 1.

    Examples
    --------
    >>> policy_year("2026-02-01", "2027-01-31")
    1
    >>> policy_year("2026-02-01", "2027-02-01")
    2
    """
    issue = to_ts(issue)
    val = to_ts(val)
    if pd.isna(issue) or pd.isna(val):
        return 1
    year_num = val.year - issue.year + 1
    anniversary = safe_replace_year(issue, val.year)
    if val < anniversary:
        year_num -= 1
    return max(year_num, 1)


# ---------------------------------------------------------------------------
# Month difference
# ---------------------------------------------------------------------------

def month_diff(start: Any, end: Any) -> int:
    """
    Return the whole-month count between *start* and *end*.

    A partial month at the end is not counted (i.e. the result is the
    number of *complete* months).  Returns 0 if either date is missing
    or if *end* is before *start*.

    Used for ``RemainingMonthsInGuaranteePeriod``.

    Examples
    --------
    >>> month_diff("2026-02-01", "2027-02-01")
    12
    >>> month_diff("2026-02-15", "2027-02-14")
    11
    """
    start = to_ts(start)
    end = to_ts(end)
    if pd.isna(start) or pd.isna(end):
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


# ---------------------------------------------------------------------------
# Surrender charge lookup
# ---------------------------------------------------------------------------

def sc_rate(sc_table: Optional[pd.DataFrame], year_num: int) -> float:
    """
    Return the surrender charge rate (as a decimal) for *year_num*.

    Looks up the ``SurrenderCharges`` table loaded from the input workbook.
    If the table is missing or the year is not found, returns ``0.0``.

    Parameters
    ----------
    sc_table :
        DataFrame with columns ``["Year", "ChargeRate"]`` where
        ``ChargeRate`` is already stored as a decimal.
    year_num :
        The 1-based policy year to look up.

    Returns
    -------
    float
        Surrender charge rate as a decimal (e.g. ``0.08`` for 8 %).
    """
    if sc_table is None or sc_table.empty:
        return 0.0
    match = sc_table.loc[sc_table["Year"] == year_num, "ChargeRate"]
    return float(match.iloc[0]) if not match.empty else 0.0


# ---------------------------------------------------------------------------
# Standard derived-fields snapshot
# ---------------------------------------------------------------------------

def snapshot(
    val_date: Any,
    av: float,
    issue_dt: Any,
    gp_end: Any,
    sc_tbl: Optional[pd.DataFrame],
) -> Dict[str, Any]:
    """
    Build the standard set of derived balance fields for a valuation date.

    Computes:
    - ``SurrenderChargeRate``
    - ``SurrenderCharge``
    - ``MVA``
    - ``CashSurrenderValue``
    - ``RemainingMonthsInGuaranteePeriod``

    Parameters
    ----------
    val_date :
        The valuation date for which the snapshot is taken.
    av :
        Account value at this valuation date.
    issue_dt :
        Policy issue date (used to determine the policy year).
    gp_end :
        Guarantee period end date (used for remaining-months calculation).
    sc_tbl :
        Surrender charge lookup table (``DataFrame`` or ``None``).

    Returns
    -------
    dict
        Keys match the standard output fields for these derived values.
    """
    year_num = policy_year(issue_dt, val_date)
    rate = sc_rate(sc_tbl, year_num)
    surrender_charge = av * rate
    mva = 0.0  # MVA formula is a future extension
    csv = av + mva - surrender_charge
    rem_months = month_diff(val_date, gp_end)
    return {
        "SurrenderChargeRate": rate,
        "SurrenderCharge": surrender_charge,
        "MVA": mva,
        "CashSurrenderValue": csv,
        "RemainingMonthsInGuaranteePeriod": rem_months,
    }


# ---------------------------------------------------------------------------
# Maturity date
# ---------------------------------------------------------------------------

def maturity_date_from_issue_and_annuitant(
    issue_dt: Any,
    annuitant_dob: Any,
) -> pd.Timestamp:
    """
    Compute the maturity date as the first policy anniversary on or after
    the annuitant reaches age 100.

    If either date is missing, returns ``pd.NaT``.

    Parameters
    ----------
    issue_dt :
        Policy issue date.
    annuitant_dob :
        Annuitant date of birth.

    Returns
    -------
    pd.Timestamp
        Maturity date, or ``pd.NaT`` if it cannot be determined.

    Notes
    -----
    The returned date is always a policy anniversary (same month and day
    as ``issue_dt``) because annuity contracts mature on their anniversary.
    """
    issue_dt = to_ts(issue_dt)
    annuitant_dob = to_ts(annuitant_dob)
    if pd.isna(issue_dt) or pd.isna(annuitant_dob):
        return pd.NaT
    age100 = safe_replace_year(annuitant_dob, annuitant_dob.year + 100)
    candidate = safe_replace_year(issue_dt, age100.year)
    if candidate < age100:
        candidate = safe_replace_year(issue_dt, age100.year + 1)
    return candidate
