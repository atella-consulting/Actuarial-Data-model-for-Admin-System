"""
config.py
---------
Shared constants and configuration for the MYGA/FIA actuarial engine.

All values here are module-level constants — nothing is computed at
runtime (except converting today's date to a Timestamp).

Sections
--------
  TODAY              - pandas Timestamp of today's date, used in validation
  Default rates      - fallback values when input/lookup is missing
  PLAN_YEARS         - product term → guarantee years mapping
  FIELDS             - master ordered list of all output fields
  FIELD_DOMAIN       - field → category label (for the output sheet)
  RATE_FIELDS        - set of fields that are displayed as percentages
  STATIC_CARRY       - fields copied forward unchanged by roll_forward()
"""

from datetime import date
import pandas as pd

# Store today's date as a pandas Timestamp.
# Used in validation to check whether IssueDate is in a valid range.
TODAY = pd.Timestamp(date.today())

# ---------------------------------------------------------------------------
# Hardcoded default rates
# ---------------------------------------------------------------------------
# These are used when input or lookup values are missing.

GMIR             = 0.01
NONFORFEITURE    = 0.024
MVA_REF_RATE     = 0.042
PREMIUM_TAX_RATE = 0.0

# ---------------------------------------------------------------------------
# Plan years
# ---------------------------------------------------------------------------
# Maps product term code to number of guarantee years.
# Example: product type "5" → 5-year guarantee period.

PLAN_YEARS: dict = {
    "3":  3,
    "5":  5,
    "7":  7,
    "10": 10,
}

# ---------------------------------------------------------------------------
# Output field list
# ---------------------------------------------------------------------------
# Master list of all output fields used in the model.
# Fields appear as rows in the final output workbook, in this order.

FIELDS: list = [
    "ValuationDate",
    "PolicyNumber",
    "IssueDate",
    "ProductType",
    "PlanCode",
    "IssueAge",
    "State",
    "SinglePremium",
    "SelectedRiders",
    "GuaranteedMinimumInterestRate",
    "NonforfeitureRate",
    "MaturityDate",
    "AnnuitantDOB",
    "OwnerDOB",
    "PremiumTaxRate",
    "GuaranteePeriodStartDate",
    "GuaranteePeriodEndDate",
    "CurrentCreditRate",
    "MVAReferenceRateAtStart",
    "AccumulatedInterestCurrentYear",
    "PenaltyFreeWithdrawalBalance",
    "RemainingMonthsInGuaranteePeriod",
    "AccountValue",
    "SurrenderChargeRate",
    "SurrenderCharge",
    "MVA",
    "CashSurrenderValue",
    "DailyInterest",
    "GrossWD",
    "Net",
    "Tax",
]

# ---------------------------------------------------------------------------
# Field domain / category
# ---------------------------------------------------------------------------
# Classifies each field into a simple business category.
# Used for display / documentation in the output sheet.

FIELD_DOMAIN: dict = {
    "ValuationDate":                   "Date",
    "PolicyNumber":                    "Identity",
    "IssueDate":                       "Date",
    "ProductType":                     "Static",
    "PlanCode":                        "Static",
    "IssueAge":                        "Static",
    "State":                           "Static",
    "SinglePremium":                   "Static",
    "SelectedRiders":                  "Static",
    "GuaranteedMinimumInterestRate":   "Rate",
    "NonforfeitureRate":               "Rate",
    "MaturityDate":                    "Date",
    "AnnuitantDOB":                    "Date",
    "OwnerDOB":                        "Date",
    "PremiumTaxRate":                  "Rate",
    "GuaranteePeriodStartDate":        "Date",
    "GuaranteePeriodEndDate":          "Date",
    "CurrentCreditRate":               "Rate",
    "MVAReferenceRateAtStart":         "Rate",
    "AccumulatedInterestCurrentYear":  "Balance",
    "PenaltyFreeWithdrawalBalance":    "Balance",
    "RemainingMonthsInGuaranteePeriod": "Derived",
    "AccountValue":                    "Balance",
    "SurrenderChargeRate":             "Rate",
    "SurrenderCharge":                 "Balance",
    "MVA":                             "Balance",
    "CashSurrenderValue":              "Balance",
    "DailyInterest":                   "Balance",
    "GrossWD":                         "Transaction",
    "Net":                             "Transaction",
    "Tax":                             "Transaction",
}

# ---------------------------------------------------------------------------
# Rate fields
# ---------------------------------------------------------------------------
# Fields whose values are decimal rates.
# fmt_output() in utils.py renders these as "5.7500%" instead of 0.0575.

RATE_FIELDS: set = {
    "GuaranteedMinimumInterestRate",
    "NonforfeitureRate",
    "PremiumTaxRate",
    "CurrentCreditRate",
    "MVAReferenceRateAtStart",
    "SurrenderChargeRate",
}

# ---------------------------------------------------------------------------
# Static carry fields
# ---------------------------------------------------------------------------
# Fields that roll_forward() copies unchanged from the prior EOD state
# into the next valuation state, unless an event explicitly updates them.

STATIC_CARRY: list = [
    "PolicyNumber",
    "IssueDate",
    "ProductType",
    "PlanCode",
    "IssueAge",
    "State",
    "SinglePremium",
    "SelectedRiders",
    "GuaranteedMinimumInterestRate",
    "NonforfeitureRate",
    "MaturityDate",
    "AnnuitantDOB",
    "OwnerDOB",
    "PremiumTaxRate",
    "GuaranteePeriodStartDate",
    "GuaranteePeriodEndDate",
    "CurrentCreditRate",
    "MVAReferenceRateAtStart",
    "AccumulatedInterestCurrentYear",
    "PenaltyFreeWithdrawalBalance",
]
