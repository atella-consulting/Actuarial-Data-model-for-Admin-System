"""
validation.py
-------------
Field-level validation rules for the MYGA/FIA actuarial engine.

Each public function receives the data that has already been parsed and
returns a :class:`~models.ValidationResult` object.  Callers decide
whether to raise, log, or continue based on ``result.has_errors()``.

Message prefix convention (enforced by ValidationResult helpers)
----------------------------------------------------------------
  ``"E: ..."``  ->  fatal error   — event processing must stop
  ``"W: ..."``  ->  warning       — processing continues with a note

Functions
---------
  validate_initialization  - validate Event 1 (policy issue) inputs
  validate_withdrawal      - validate Event 2 (partial withdrawal) inputs
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from config import TODAY, MVA_MIN_REF_RATE, MVA_MAX_REF_RATE, PLAN_YEARS, ALLOWED_STATES, NONFORFEITURE_MIN, NONFORFEITURE_MAX
from models import ValidationResult
from utils import sfloat, nonempty, to_ts


# ---------------------------------------------------------------------------
# Event 1 — Policy Issue / Initialization
# ---------------------------------------------------------------------------

def validate_initialization(
    issue_dt: Optional[pd.Timestamp],
    primary_issue_age: Optional[float],
    premium: float,
    AccumulatedInterestCurrentYear: Optional[float] = None,
    product_type: Optional[str] = None,
    state: Optional[str] = None,
    *,
    lookup_ccr: Optional[float] = None,
    lookup_gmir: Optional[float] = None,
    lookup_nonforf: Optional[float] = None,
    lookup_date: Optional[pd.Timestamp] = None,
) -> ValidationResult:
    """
    Validate the core fields read during policy initialization (Event 1).

    Rules
    -----
    - IssueDate must be present and fall in [2020-01-01, today]  → E
    - Primary_IssueAge must be in [0, 95] when provided          → W
    - SinglePremium must be in [10,000, 1,000,000]               → W
    - AccumulatedInterestCurrentYear must be in [10,000, 1,000,000] if provided → W

    Returns
    -------
    ValidationResult
        Contains all messages found; may have both errors and warnings.
    """
    result = ValidationResult()

    # --- IssueDate ---
    if issue_dt is None or (isinstance(issue_dt, pd.Timestamp) and pd.isna(issue_dt)):
        result.add_error("IssueDate", "IssueDate missing")
    elif not (pd.Timestamp("2020-01-01") <= issue_dt <= TODAY):
        result.add_error(
            "IssueDate",
            f"IssueDate outside [2020-01-01 ; {TODAY.date()}]",
        )

    # --- Primary_IssueAge ---
    if primary_issue_age is not None and not (0 <= primary_issue_age <= 95):
        result.add_warning("Primary_IssueAge", "Primary_IssueAge outside expected range [0 ; 95]")
    elif primary_issue_age is None:
        result.add_warning("Primary_IssueAge", "Primary_IssueAge not provided")

    # --- SinglePremium ---
    if premium < 10_000 or premium > 1_000_000:
        result.add_warning(
            "SinglePremium",
            "SinglePremium outside recommended range [10,000 ; 1,000,000]",
        )

    # --- AccumulatedInterestCurrentYear ---
    if AccumulatedInterestCurrentYear is not None and AccumulatedInterestCurrentYear != 0.0:
        if AccumulatedInterestCurrentYear < 10_000 or AccumulatedInterestCurrentYear > 1_000_000:
            result.add_warning(
                "AccumulatedInterestCurrentYear",
                "AccumulatedInterestCurrentYear outside recommended range [10,000 ; 1,000,000]",
            )

    # --- ProductType ---
    if product_type not in PLAN_YEARS:
        result.add_error(
            "ProductType",
            "ProductType must be one of: MYGA_03, MYGA_05, MYGA_07, MYGA_10"
        )

    # --- NonforfeitureRate ---
    if lookup_nonforf is not None and not (NONFORFEITURE_MIN <= lookup_nonforf <= NONFORFEITURE_MAX):
        result.add_warning(
            "NonforfeitureRate",
            f"NonforfeitureRate ({lookup_nonforf:.6f}) outside configured range "
            f"[{NONFORFEITURE_MIN:.6f} ; {NONFORFEITURE_MAX:.6f}]"
        )

    # --- State ---
    if state is not None:
        state = str(state).strip().upper()
        if state not in ALLOWED_STATES:
            result.add_warning(
                "State",
                f"State ({state}) is not in the allowed state list"
            )

    # --- ProductTables lookup warnings ---
    if lookup_date is not None:
        lookup_date_str = (
            lookup_date.date() if hasattr(lookup_date, "date") else lookup_date
        )

        if lookup_ccr is None:
            result.add_warning(
                "CurrentCreditRate",
                f"No ProductTables match found for CreditingRate / {product_type} / <= {lookup_date_str}"
            )

        if lookup_gmir is None:
            result.add_warning(
                "GuaranteedMinimumInterestRate",
                f"No ProductTables match found for GuaranteedMinimumInterestRate / {product_type} / <= {lookup_date_str}"
            )

        if lookup_nonforf is None:
            result.add_warning(
                "NonforfeitureRate",
                f"No ProductTables match found for NonforfeitureRate / {product_type} / <= {lookup_date_str}"
            )
    return result




# ---------------------------------------------------------------------------
# Event 2 — Partial Withdrawal
# ---------------------------------------------------------------------------

def validate_withdrawal(
    gross_wd: float,
    pre_av: float,
    pfwb: float,
    rate_at_start: Optional[float] = None,
    rate_current: Optional[float] = None,
) -> ValidationResult:
    """
    Validate the fields read during partial-withdrawal processing (Event 2).

    Rules
    -----
    - Event 2 valuation date is system-derived from Event 1 valuation date + 1 day
    - GrossWD > AccountValue → withdrawal exceeds account value   → E
    - GrossWD > PFWB         → withdrawal exceeds penalty-free
                               withdrawal balance                  → W
    - rate_at_start missing  → cannot compute MVA                 → E
    - rate_at_start not in [MVA_MIN_REF_RATE, MVA_MAX_REF_RATE]     → W
    - rate_current not in [MVA_MIN_REF_RATE, MVA_MAX_REF_RATE]      → W
    - |rate_current - rate_at_start| > 0.10 → large rate change (>10 pp)         → W
    """
    result = ValidationResult()

    # --- GrossWD vs AccountValue (fatal) ---
    if gross_wd > pre_av:
        result.add_error(
            "GrossWD",
            f"GrossWD ({gross_wd:,.2f}) exceeds AccountValue ({pre_av:,.2f})",
        )
    # --- GrossWD vs PFWB (warning only) ---
    elif gross_wd > pfwb:
        result.add_warning(
            "GrossWD",
            f"GrossWD ({gross_wd:,.2f}) exceeds "
            f"PenaltyFreeWithdrawalBalance ({pfwb:,.2f})",
        )

    # --- MVA reference rate validation (only when excess withdrawal exists) ---
    excess_exists = gross_wd > pfwb

    if excess_exists:
        # Missing rate at start → MVA cannot be computed
        if rate_at_start is None:
            result.add_error(
                "MVAReferenceRateAtStart",
                "MVA reference rate at guarantee period start is missing; "
                "cannot compute MVA on excess withdrawal",
            )
        else:
            if not (MVA_MIN_REF_RATE <= rate_at_start <= MVA_MAX_REF_RATE):
                result.add_warning(
                    "MVAReferenceRateAtStart",
                    f"MVAReferenceRateAtStart ({rate_at_start:.4%}) is outside "
                    f"expected range [{MVA_MIN_REF_RATE:.0%}, {MVA_MAX_REF_RATE:.0%}]",
                )

        # Missing current rate
        if rate_current is None:
            result.add_error(
                "MVAReferenceRateCurrent",
                "MVA reference rate for the day preceding the valuation date is "
                "missing; cannot compute MVA on excess withdrawal",
            )
        else:
            if not (MVA_MIN_REF_RATE <= rate_current <= MVA_MAX_REF_RATE):
                result.add_warning(
                    "MVAReferenceRateCurrent",
                    f"MVA current reference rate ({rate_current:.4%}) is outside "
                    f"expected range [{MVA_MIN_REF_RATE:.0%}, {MVA_MAX_REF_RATE:.0%}]",
                )

        # Large rate change warning (>10 percentage points)
        if rate_at_start is not None and rate_current is not None:
            rate_change = abs(rate_current - rate_at_start)
            if rate_change > 0.10:
                result.add_warning(
                    "MVARateChange",
                    f"MVA rate change ({rate_change:.4%}) exceeds 10 percentage "
                    "points; verify reference rates are correct",
                )

    return result