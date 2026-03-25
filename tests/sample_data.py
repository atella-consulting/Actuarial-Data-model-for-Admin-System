"""
tests/fixtures.py
-----------------
Shared sample test data and factory helpers used across all test modules.
"""

from __future__ import annotations
import pandas as pd

# ---------------------------------------------------------------------------
# Sample Input Data
# ---------------------------------------------------------------------------

POLICY_NUMBER  = "TEST-001"
ISSUE_DATE     = "2026-01-15"
ANNUITANT_DOB  = "1960-03-01"
OWNER_DOB      = "1962-07-04"
SINGLE_PREMIUM = 100_000.0
PRODUCT_TYPE   = "5"
ISSUE_AGE      = 65
STATE          = "TX"

CREDIT_RATE    = 0.0575
GMIR           = 0.01
NONFORFEITURE  = 0.024
MVA_REF_RATE   = 0.042
PREMIUM_TAX    = 0.0

GP_START       = "2026-01-15"
GP_END         = "2031-01-15"
MATURITY_DATE  = "2061-01-15"

SC_RATES = {1: 0.08, 2: 0.07, 3: 0.06, 4: 0.05, 5: 0.04}

WD_VALUATION_DATE = "2026-06-15"
GROSS_WD          = 5_000.0
NET_WD            = 4_750.0
TAX_WD            = 250.0


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def policy_row(
    *,
    include_withdrawal: bool = False,
    gross_wd: float = GROSS_WD,
    wd_date: str = WD_VALUATION_DATE,
    issue_date: str = ISSUE_DATE,
    premium: float = SINGLE_PREMIUM,
    product_type: str = PRODUCT_TYPE,
    issue_age: int = ISSUE_AGE,
    credit_rate: str = "5.75%",
    state: str = STATE,
) -> pd.Series:
    """creates a fake policy record with the specified parameters, optionally including withdrawal data."""
    data = {
        "PolicyNumber":      POLICY_NUMBER,
        "IssueDate":         issue_date,
        "Valuation Date":    issue_date,
        "AnnuitantDOB":      ANNUITANT_DOB,
        "OwnerDOB":          OWNER_DOB,
        "SinglePremium":     premium,
        "ProductType":       product_type,
        "PlanCode":          product_type,
        "IssueAge":          issue_age,
        "State":             state,
        "CurrentCreditRate": credit_rate,
    }
    if include_withdrawal:
        data["Valuation Date.1"] = wd_date
        data["Gross WD"]         = gross_wd
        data["Net"]              = NET_WD
        data["Tax"]              = TAX_WD
    return pd.Series(data)


def sc_table() -> pd.DataFrame:
    """creates a fake surrender charge table based on the SC_RATES dict."""
    return pd.DataFrame(
        [{"Year": yr, "ChargeRate": rate} for yr, rate in SC_RATES.items()]
    )


def product_tables(credit_rate: str = "5.75%") -> dict:
    """creates fake product setup data with the specified crediting rate."""
    return {"CreditingRate": {"5-year": credit_rate}}

import pandas as pd

def mva_rates_table() -> pd.DataFrame:
    """
    Fake market-rate table used for MVA lookups.
    Must contain:
      - issue date for Event 1 start-rate lookup
      - day before withdrawal date for Event 2 current-rate lookup
      - columns for 3/5/7/10-year products
    """
    idx = pd.to_datetime([
        "2026-01-15",  # issue / gp start
        "2026-06-14",  # day before WD_VALUATION_DATE = 2026-06-15
    ])

    return pd.DataFrame(
        {
            "Y03": [0.040, 0.041],
            "Y05": [0.042, 0.043],
            "Y07": [0.044, 0.045],
            "Y10": [0.046, 0.047],
        },
        index=idx,
    )