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

from typing import Any, Dict, List, Optional

import pandas as pd

from utils import to_ts, to_pct, sfloat, safe_replace_year, nonempty

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


def parse_selected_riders(selected_riders: Any) -> List[str]:
    """
    Parse and normalize the SelectedRiders field into rider codes.

    Normalization rules:
      - split by comma
      - trim whitespace
      - uppercase
      - deduplicate while preserving order
    """
    if not nonempty(selected_riders):
        return []

    riders: List[str] = []
    seen = set()

    for token in str(selected_riders).split(","):
        code = token.strip().upper()
        if not code:
            continue
        if code not in seen:
            seen.add(code)
            riders.append(code)

    return riders


def lookup_rider_fee_rate(
    product_tables: pd.DataFrame,
    rider_table_name: str,
    product_type: str,
    valuation_date: Any,
) -> Optional[float]:
    """
    Lookup a rider fee using ProductTables date-effective logic.

    Required ProductTables layout:
      TableName = "RiderFee", ProductType = rider table name
    """
    return lookup_product_table_rate(
        product_tables=product_tables,
        table_name="RiderFee",
        product_type=rider_table_name,
        valuation_date=valuation_date,
    )


def rider_credit_rate_adjustment(
    product_tables: pd.DataFrame,
    product_type: str,
    valuation_date: Any,
    selected_riders: Any,
) -> Dict[str, Any]:
    """
    Resolve rider fees and validation conflicts for SelectedRiders.

    Rider mapping to ProductTables table names:
      DBR  -> DeathBenefit
      5WR  -> 5FreeWD
      IWR  -> InterestWD
      EIWR -> EnhInterestWD      (placeholder, not applied yet)
      LBR  -> placeholder         (not applied yet)
      ELBR -> EnhBenefitWD
    """
    rider_to_table = {
        "DBR": "DeathBenefit",
        "5WR": "5FreeWD",
        "IWR": "InterestWD",
        "EIWR": "EnhInterestWD",
        "ELBR": "EnhBenefitWD",
    }

    riders = parse_selected_riders(selected_riders)
    rider_set = set(riders)
    applied_fees: Dict[str, float] = {}

    for rider in riders:
        # Placeholder only (not applied yet)
        if rider in {"LBR", "EIWR"}:
            continue

        table_name = rider_to_table.get(rider)
        if not table_name:
            continue

        fee = lookup_rider_fee_rate(
            product_tables=product_tables,
            rider_table_name=table_name,
            product_type=product_type,
            valuation_date=valuation_date,
        )
        if fee is not None:
            applied_fees[rider] = fee

    conflicts: List[str] = []
    if {"ELBR", "LBR"}.issubset(rider_set):
        conflicts.append("ELBR cannot be selected together with LBR")
    if {"EIWR", "IWR"}.issubset(rider_set):
        conflicts.append("EIWR cannot be selected together with IWR")

    return {
        "riders": riders,
        "applied_fees": applied_fees,
        "total_fee": float(sum(applied_fees.values())),
        "conflicts": conflicts,
    }


def has_death_benefit_rider(selected_riders: Any) -> bool:
    """Return True when DBR is present in SelectedRiders."""
    return "DBR" in set(parse_selected_riders(selected_riders))


def compute_death_benefit_amount(
    selected_riders: Any,
    accumulation_value: Any,
    cash_surrender_value: Any,
) -> float:
    """
    Compute Death_Benefit_Amount by rider rule:
      - DBR selected: use accumulation value (AccountValue), no MVA impact
      - DBR not selected: use cash surrender value (includes MVA when present)
    """
    av = sfloat(accumulation_value, 0.0)
    csv = sfloat(cash_surrender_value, av)
    return av if has_death_benefit_rider(selected_riders) else csv

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
# Required Minimum Distribution (RMD)
# ---------------------------------------------------------------------------
def required_rmd_start_age(owner_dob: Any) -> Optional[float]:
    """Return the statutory starting age for lifetime RMDs by birth cohort."""
    dob = to_ts(owner_dob)
    if pd.isna(dob):
        return None
    if dob.year < 1951:
        return 70.5
    if 1951 <= dob.year <= 1959:
        return 73.0
    return 75.0


def attained_age(owner_dob: Any, as_of_date: Any) -> Optional[int]:
    """Return age on last birthday as of *as_of_date*."""
    dob = to_ts(owner_dob)
    as_of = to_ts(as_of_date)
    if pd.isna(dob) or pd.isna(as_of):
        return None
    return int(as_of.year - dob.year - ((as_of.month, as_of.day) < (dob.month, dob.day)))


def calculate_issue_age(annuitant_dob: Any, issue_date: Any) -> Optional[int]:
    """Return issue age (age on last birthday as of issue date)."""
    return attained_age(annuitant_dob, issue_date)


def calculate_rmd(
    owner_dob: Any,
    valuation_date: Any,
    prior_year_end_balance: Any,
    rmd_qualified: Any,
    rmd_table: Optional[pd.DataFrame],
) -> float:
    """
    Calculate RMD using the IRS distribution-period table.

    Rules implemented:
      - If RMD_Qualified is not ``"Y"``, return ``0.0`` immediately.
      - If the owner is below the required start age, use the earliest eligible
        age supported by the supplied table.
      - Otherwise use next year's age.
      - Numerator is the balance as of December 31 of the previous year.
    """
    qual = str(rmd_qualified).strip().upper() if rmd_qualified is not None else ""
    if qual != "Y":
        return 0.0

    balance = sfloat(prior_year_end_balance, None)
    if balance is None:
        raise ValueError("PriorYearEndAccountValue is required when RMD_Qualified = 'Y'.")

    if rmd_table is None or rmd_table.empty:
        raise ValueError("RMD table is required when RMD_Qualified = 'Y'.")
    if "Age" not in rmd_table.columns or "Distribution Period" not in rmd_table.columns:
        raise ValueError("RMD table must contain 'Age' and 'Distribution Period' columns.")

    start_age = required_rmd_start_age(owner_dob)
    current_age = attained_age(owner_dob, valuation_date)
    if start_age is None or current_age is None:
        raise ValueError("OwnerDOB and ValuationDate are required when RMD_Qualified = 'Y'.")

    table = rmd_table[["Age", "Distribution Period"]].copy()
    table["Age"] = pd.to_numeric(table["Age"], errors="coerce")
    table["Distribution Period"] = pd.to_numeric(table["Distribution Period"], errors="coerce")
    table = table.dropna(subset=["Age", "Distribution Period"]).sort_values("Age")
    if table.empty:
        raise ValueError("RMD table does not contain any usable rows.")

    table_min_age = int(table["Age"].iloc[0])
    table_max_age = int(table["Age"].iloc[-1])

    if current_age < start_age:
        lookup_age = max(table_min_age, int(start_age if float(start_age).is_integer() else int(start_age) + 1))
    else:
        lookup_age = current_age + 1

    lookup_age = min(max(lookup_age, table_min_age), table_max_age)

    row = table.loc[table["Age"] == lookup_age, "Distribution Period"]
    if row.empty:
        raise ValueError(f"No RMD distribution period found for age {lookup_age}.")

    factor = float(row.iloc[0])
    if factor <= 0:
        raise ValueError(f"Invalid RMD distribution period for age {lookup_age}: {factor}.")

    return balance / factor


def free_withdrawal_components(
    accumulated_interest_current_year: Any,
    rmd: Any,
    *,
    tax_qualified: Any = None,
    rmd_qualified: Any = None,
) -> Dict[str, Any]:
    """
    Return Interest Withdrawal Rider components and free amount.

    Rule:
      A = max(0, AccumulatedInterestCurrentYear)
      B = max(0, RMD) only when tax-qualified
      Free_Withdrawal_Amount = max(A, B)

    Tax qualification flag precedence:
      1) Tax_Qualified
      2) RMD_Qualified (fallback when Tax_Qualified missing)
    """
    a = max(0.0, sfloat(accumulated_interest_current_year, 0.0))

    tax_raw = tax_qualified if nonempty(tax_qualified) else rmd_qualified
    tax_flag = str(tax_raw).strip().upper() == "Y"

    b = max(0.0, sfloat(rmd, 0.0)) if tax_flag else 0.0

    return {
        "a": a,
        "b": b,
        "tax_qualified": tax_flag,
        "free_withdrawal_amount": max(a, b),
    }

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
    mva = 0.0  # MVA is applied separately in event processors; snapshot provides the base CSV only
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
    For joint-life products, this function still uses the primary annuitant
    date of birth only; that assumption is a product / business-rule choice.
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
