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

from utils import to_ts, to_pct, sfloat, safe_replace_year

from config import MVA_DATE_COLUMN, MVA_RATE_COLUMNS, MVA_PLAN_TO_COLUMN, MVA_WAIVER_DAYS, MVA_MIN_REF_RATE, MVA_MAX_REF_RATE


# ---------------------------------------------------------------------------
# MVA market-rate helpers
# ---------------------------------------------------------------------------
def resolve_mva_column(plan_years: int) -> str:
    """
    Return the rate-file column name that corresponds to *plan_years*.

    Uses the ``MVA_PLAN_TO_COLUMN`` bracket table defined in ``config.py``.
    """
    for upper_bound, col in MVA_PLAN_TO_COLUMN:
        if plan_years <= upper_bound:
            return col
    # Map plan_years to the first MVA bucket whose upper bound is >= plan_years
    return MVA_PLAN_TO_COLUMN[-1][1]


def get_mva_rate(
    rates_df: pd.DataFrame,
    date: Any,
    column: Optional[str] = None,
) -> Optional[float]:
    if rates_df is None or rates_df.empty:
        return None

    if column is None or column not in rates_df.columns:
        return None
    col = column

    ts = to_ts(date)
    if pd.isna(ts):
        return None

    for offset in range(0, 3):
        candidate = ts - pd.Timedelta(days=offset)
        if candidate in rates_df.index:
            val = rates_df.at[candidate, col]

            if isinstance(val, pd.Series):
                val = val.dropna()
                if val.empty:
                    continue
                return float(val.iloc[0])

            if pd.notna(val):
                return float(val)

    return None

def lookup_product_table_rate(
    product_tables: pd.DataFrame,
    table_name: str,
    product_type: str,
    valuation_date: Any,
) -> Optional[float]:
    """
    Return the ProductTables rate for the given table/product/date.

    Rule:
      choose the row with the latest EffectiveDate such that
      EffectiveDate <= valuation_date.
    """
    if product_tables is None or product_tables.empty:
        return None

    val_ts = to_ts(valuation_date)
    if pd.isna(val_ts):
        return None

    product_type = "" if product_type is None else str(product_type).strip()
    table_name = "" if table_name is None else str(table_name).strip()

    eligible = product_tables[
        (product_tables["TableName"] == table_name)
        & (product_tables["ProductType"] == product_type)
        & (product_tables["EffectiveDate"] <= val_ts)
    ].copy()

    if eligible.empty:
        return None

    eligible = eligible.sort_values("EffectiveDate")
    return to_pct(eligible.iloc[-1]["Value"])

def is_mva_waiver_window(
    val_date: Any,
    gp_start: Any,
    waiver_days: int = MVA_WAIVER_DAYS,
) -> bool:
    """
    Return ``True`` if *val_date* falls within the MVA waiver window.
    The waiver window is [gp_start, gp_start + waiver_days).
    """
    val_ts  = to_ts(val_date)
    gp_ts   = to_ts(gp_start)
    if pd.isna(val_ts) or pd.isna(gp_ts):
        return False
    window_end = gp_ts + pd.Timedelta(days=waiver_days)
    return gp_ts <= val_ts < window_end


def compute_mva(
    excess_amount: float,
    rate_at_start: float,
    rate_current: float,
    remaining_months: int,
) -> float:
    """
    Compute the Market Value Adjustment dollar amount.

    Formula:
        mva_factor = ((1 + A) / (1 + B)) ** t  -  1
        MVA        = excess_amount * mva_factor

    Where:
      - A = ``rate_at_start``   — reference rate at the beginning of the
            current guarantee period (decimal, e.g. 0.05 for 5 %)
      - B = ``rate_current``    — reference rate on the day preceding the
            valuation date (decimal)
      - t = ``remaining_months`` / 12  — whole months remaining in the guarantee period, expressed in years (e.g. 18 months → 1.5)
    """
    if remaining_months <= 0 or excess_amount <= 0:
        return 0.0

    t = remaining_months / 12.0
    denominator = 1.0 + rate_current

    # Guard against division by zero
    if denominator == 0:
        return 0.0

    mva_factor = ((1.0 + rate_at_start) / denominator) ** t - 1.0
    return excess_amount * mva_factor


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