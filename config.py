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
PREMIUM_TAX_RATE = 0.0
MVA_MIN_REF_RATE = 0.00
MVA_MAX_REF_RATE = 0.10
MGSV_BASE_PERCENTAGE = 0.875
MGSV_CONTRACT_CHARGE = 50.0
NONFORFEITURE_MIN = 0.0025
NONFORFEITURE_MAX = 0.03

# ---------------------------------------------------------------------------
# Annuitization table settings
# ---------------------------------------------------------------------------
ANNUITY_CSV_FOLDER = "CSV_Files"
ANNUITY_MORTALITY_TABLE = "2012_Period_Table_IAM2012.csv"
ANNUITY_PROJECTION_TABLE = "Projection_Scale_G2.csv"

# ---------------------------------------------------------------------------
# MVA market-rate data settings
# ---------------------------------------------------------------------------
# Number of calendar days at the *start* of each guarantee period during
# which the MVA is waived
MVA_WAIVER_DAYS: int = 30
# Date column name in the MVA_Table tab
MVA_DATE_COLUMN: str = "MDATE"
MVA_RATE_COLUMNS: list = [
    "M01", "M03", "M06",
    "Y01", "Y02", "Y03",
    "Y05", "Y07", "Y10",
    "Y20", "Y30",
]


MVA_PLAN_TO_COLUMN: list = [
    (1,  "Y01"),
    (2,  "Y02"),
    (3,  "Y03"),
    (5,  "Y05"),
    (7,  "Y07"),
    (10, "Y10"),
    (20, "Y20"),
    (30, "Y30"),
]

# ---------------------------------------------------------------------------
# Plan years
# ---------------------------------------------------------------------------
# Maps product term code to number of guarantee years.
# Example: product type "5" → 5-year guarantee period.

PLAN_YEARS: dict = {
    "MYGA_03":  3,
    "MYGA_05":  5,
    "MYGA_07":  7,
    "MYGA_10": 10,
}

# ---------------------------------------------------------------------------
# Allowed states
# ---------------------------------------------------------------------------
ALLOWED_STATES: set = {
    "AL","AK","AZ","AR","CO","DE","DC","GA","HI","ID","IL","IN","IA","KS","KY",
    "ME","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NC","ND","OH","OK",
    "OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY"
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
    "EffectiveDate",
    "AnniversaryDateNext",
    "ProductType",
    "PlanCode",

    "Primary_IssueAge",
    "Primary_Sex",
    "Secondary_IssueAge",
    "Secondary_Sex",
    "TermCertain",
    "AnnuityType",

    "State",
    "SinglePremium",
    "SelectedRiders",
    "GuaranteedMinimumInterestRate",
    "NonforfeitureRate",
    "GuaranteedMinimumAV",
    "MaturityDate",
    "AnnuitantDOB",
    "OwnerDOB",
    "PremiumTaxRate",
    "GuaranteePeriodStartDate",
    "GuaranteePeriodEndDate",
    "Term_Period",
    "CurrentCreditRate",
    "MVAReferenceRateAtStart",
    "AccumulatedInterestCurrentYear",
    "PenaltyFreeWithdrawalBalance",
    "RemainingMonthsInGuaranteePeriod",
    "AccountValue",
    "RMD",
    "SurrenderChargeRate",
    "SurrenderCharge",
    "MVA",
    "CashSurrenderValue",
    "DailyInterest",
    "GrossWD",
    "Net",
    "Tax",
    "PV Expected Benefits",
    "Purchase Rate per $1,000",
    "Modal Benefit @ Issue",
]

# ---------------------------------------------------------------------------
# Field domain / category
# ---------------------------------------------------------------------------
# Classifies each field into a simple business category.
# Used for display / documentation in the output sheet.

FIELD_DOMAIN: dict = {
    "ValuationDate":                   "Date",
    "PolicyNumber":                    "Identity",
    "IssueDate":                       "Static_Date",
    "EffectiveDate":                   "Static_Date",
    "AnniversaryDateNext":             "Date",
    "ProductType":                     "Product",
    "PlanCode":                        "Product",

    "Primary_IssueAge":               "Identity",
    "Primary_Sex":                    "Identity",
    "Secondary_IssueAge":             "Identity",
    "Secondary_Sex":                  "Identity",
    "TermCertain":                    "Annuitization",
    "AnnuityType":                    "Annuitization",

    "State":                           "Identity",
    "SinglePremium":                   "Policy",
    "SelectedRiders":                  "Policy",
    "GuaranteedMinimumInterestRate":   "Rate",
    "NonforfeitureRate":               "Rate",
    "GuaranteedMinimumAV":             "Balance",
    "MaturityDate":                    "Static_Date",
    "AnnuitantDOB":                    "Static_Date",
    "OwnerDOB":                        "Static_Date",
    "PremiumTaxRate":                  "Rate",
    "GuaranteePeriodStartDate":        "Date",
    "GuaranteePeriodEndDate":          "Date",
    "Term_Period":                     "Product",
    "CurrentCreditRate":               "Rate",
    "MVAReferenceRateAtStart":         "Rate",
    "AccumulatedInterestCurrentYear":  "Balance",
    "PenaltyFreeWithdrawalBalance":    "Balance",
    "RemainingMonthsInGuaranteePeriod": "Term",
    "AccountValue":                    "Balance",
    "RMD":                             "Balance",
    "SurrenderChargeRate":             "Rate",
    "SurrenderCharge":                 "Balance",
    "MVA":                             "Balance",
    "CashSurrenderValue":              "Balance",
    "DailyInterest":                   "Balance",
    "GrossWD":                         "Transaction",
    "Net":                             "Transaction",
    "Tax":                             "Transaction",
    "PV Expected Benefits":            "Annuitization",
    "Purchase Rate per $1,000":        "Annuitization",
    "Modal Benefit @ Issue":           "Annuitization",
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
    "EffectiveDate",
    "ProductType",
    "PlanCode",
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
    "Term_Period",
    "CurrentCreditRate",
    "MVAReferenceRateAtStart",
    "AccumulatedInterestCurrentYear",
    "PenaltyFreeWithdrawalBalance",
    "Primary_Sex",
    "Secondary_Sex",
    "TermCertain",
    "AnnuityType",
    "Primary_IssueAge",
    "Secondary_IssueAge",
    "RMD",
    "PV Expected Benefits",
    "Purchase Rate per $1,000",
    "Modal Benefit @ Issue",
]

# ---------------------------------------------------------------------------
# Audit control
# ---------------------------------------------------------------------------
# Controls whether audit rows are generated, and for which policies.
#
#   "none"     — no audit output (default for large production runs)
#   "selected" — audit only the policies listed in AUDIT_SELECTED_POLICIES
#   "all"      — audit every policy in the run

AUDIT_MODE: str = "none"
AUDIT_SELECTED_POLICIES: list = []   # e.g. [1, 4, 102]  — used when mode is "selected"

# ---------------------------------------------------------------------------
# Optional annuitization add-on
# ---------------------------------------------------------------------------
# off = run existing daily flow only
# on  = run existing daily flow, then append annuitization calculation

ANNUITIZATION_SWITCH: str = "on"
