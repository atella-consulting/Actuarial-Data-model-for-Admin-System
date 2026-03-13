from datetime import date
import pandas as pd

# Store today's date as a pandas timestamp.
# This is used in validation, for example to check whether IssueDate is in a valid range.
TODAY = pd.Timestamp(date.today())

# Hardcoded default rates used when input or lookup values are missing.
GMIR = 0.01
NONFORFEITURE = 0.024
MVA_REF_RATE = 0.042
PREMIUM_TAX_RATE = 0.0

# Map product term to number of guarantee years.
# Example: product type "5" means a 5-year guarantee period.
PLAN_YEARS = {
    "3": 3,
    "5": 5,
    "7": 7,
    "10": 10,
}

# Master list of all output fields used in the model.
# These fields will appear as rows in the final output.
FIELDS = [
    "ValuationDate",
    "Event",
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

# Classify each field into a simple business category.
# This is mainly for display / documentation in the output sheet.
FIELD_DOMAIN = {
    "ValuationDate": "Date",
    "Event": "Event",
    "PolicyNumber": "Identity",
    "IssueDate": "Date",
    "ProductType": "Static",
    "PlanCode": "Static",
    "IssueAge": "Static",
    "State": "Static",
    "SinglePremium": "Static",
    "SelectedRiders": "Static",
    "GuaranteedMinimumInterestRate": "Rate",
    "NonforfeitureRate": "Rate",
    "MaturityDate": "Date",
    "AnnuitantDOB": "Date",
    "OwnerDOB": "Date",
    "PremiumTaxRate": "Rate",
    "GuaranteePeriodStartDate": "Date",
    "GuaranteePeriodEndDate": "Date",
    "CurrentCreditRate": "Rate",
    "MVAReferenceRateAtStart": "Rate",
    "AccumulatedInterestCurrentYear": "Balance",
    "PenaltyFreeWithdrawalBalance": "Balance",
    "RemainingMonthsInGuaranteePeriod": "Derived",
    "AccountValue": "Balance",
    "SurrenderChargeRate": "Rate",
    "SurrenderCharge": "Balance",
    "MVA": "Balance",
    "CashSurrenderValue": "Balance",
    "DailyInterest": "Balance",
    "GrossWD": "Transaction",
    "Net": "Transaction",
    "Tax": "Transaction",
}

# These fields are percentages / rates.
# They may need special formatting in the output, such as showing 5.75% instead of 0.0575.
RATE_FIELDS = {
    "GuaranteedMinimumInterestRate",
    "NonforfeitureRate",
    "PremiumTaxRate",
    "CurrentCreditRate",
    "MVAReferenceRateAtStart",
    "SurrenderChargeRate",
}

# These fields are carried forward unchanged from one day's end-of-day state
# into the next day's valuation state, unless a later event changes them.
STATIC_CARRY = [
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