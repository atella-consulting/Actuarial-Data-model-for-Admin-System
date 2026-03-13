import pandas as pd

# Import shared settings and constant values from config.py.
# These include default rates, valid plan years, and lists of fields used in the output.
from config import (
    TODAY,
    GMIR,
    NONFORFEITURE,
    MVA_REF_RATE,
    PREMIUM_TAX_RATE,
    PLAN_YEARS,
    RATE_FIELDS,
    STATIC_CARRY,
)


# Convert a value into a pandas timestamp.
# If the value cannot be converted, return NaT instead of failing.
def to_ts(x):
    if isinstance(x, pd.Timestamp):
        return x
    return pd.to_datetime(x, errors="coerce")


# Convert a rate or percentage into decimal form.
# Examples:
#   "5.75%" -> 0.0575
#   5.75    -> 0.0575
#   0.0575  -> 0.0575
def to_pct(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    if isinstance(x, str):
        x = x.strip().replace(",", "")
        if x == "":
            return None
        if x.endswith("%"):
            return float(x[:-1]) / 100
        return float(x)
    x = float(x)
    return x / 100 if abs(x) > 1 else x


# Safely convert a value to float.
# If conversion fails or the value is blank, return the given default instead.
def sfloat(x, default=0.0):
    if x is None:
        return default
    try:
        v = float(x)
        return default if pd.isna(v) else v
    except Exception:
        return default


# Check whether a value is meaningfully filled in.
# This treats None, NaN, blank strings, and "nan" text as empty.
def nonempty(x):
    return x is not None and not (isinstance(x, float) and pd.isna(x)) and str(x).strip() not in ("", "nan")


# Format a date value as YYYY-MM-DD for output.
# If the value is not a valid date, return blank text.
def fmt_date(x):
    ts = to_ts(x)
    return "" if pd.isna(ts) else ts.strftime("%Y-%m-%d")


# Replace the year part of a date safely.
# This is mainly to handle leap-year dates like Feb 29.
# If the exact date does not exist in the target year, use Feb 28.
def safe_replace_year(ts, year):
    ts = to_ts(ts)
    if pd.isna(ts):
        return pd.NaT
    try:
        return ts.replace(year=year)
    except ValueError:
        return ts.replace(year=year, day=28)


# Add a number of years to a date safely.
def add_years(ts, years):
    ts = to_ts(ts)
    if pd.isna(ts):
        return pd.NaT
    return safe_replace_year(ts, ts.year + years)


# Convert a product code / plan code into a clean text code.
# Example: 5.0 becomes "5" instead of "5.0".
def as_code(x):
    if not nonempty(x):
        return ""
    if isinstance(x, (int, float)) and float(x).is_integer():
        return str(int(x))
    return str(x).strip()


# Look through a row and return the first non-empty value
# from the given list of possible column names.
# This is useful when the input file may use slightly different headers.
def pick_first(row, *names):
    for name in names:
        if name in row.index and nonempty(row[name]):
            return row[name]
    return None


# Build one full state dictionary by combining smaller blocks.
# - base: an existing state to start from
# - blocks: new dictionaries to layer on top
# - extras: final fields to force in at the end
def merge_state(*blocks, base=None, extras=None):
    state = {} if base is None else base.copy()
    for block in blocks:
        if block:
            state.update({k: v for k, v in block.items() if v is not None})
    if extras:
        state.update(extras)
    return state


# Format a value for writing to the final Excel output.
# Dates are shown as YYYY-MM-DD.
# Rate fields are shown as percentages.
def fmt_output(v, field=None):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, float) and field in RATE_FIELDS:
        return f"{v:.4%}"
    return v


# Calculate the policy year number based on issue date and valuation date.
# Example:
#   issue date = 2026-02-01
#   valuation date = 2027-01-31
#   policy year = 1
def policy_year(issue, val):
    issue = to_ts(issue)
    val = to_ts(val)
    if pd.isna(issue) or pd.isna(val):
        return 1

    year_num = val.year - issue.year + 1
    anniversary = safe_replace_year(issue, val.year)
    if val < anniversary:
        year_num -= 1

    return max(year_num, 1)


# Calculate the whole-month difference between two dates.
# Used for fields like RemainingMonthsInGuaranteePeriod.
def month_diff(start, end):
    start = to_ts(start)
    end = to_ts(end)
    if pd.isna(start) or pd.isna(end):
        return 0

    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1

    return max(months, 0)


# Read the surrender charge rate for a given policy year from the surrender charge table.
# If nothing is found, return 0.
def sc_rate(sc_table, year_num):
    if sc_table is None or sc_table.empty:
        return 0.0

    match = sc_table.loc[sc_table["Year"] == year_num, "ChargeRate"]
    return float(match.iloc[0]) if not match.empty else 0.0


# Build the standard calculated snapshot fields for a given state:
# - surrender charge rate
# - surrender charge amount
# - MVA
# - cash surrender value
# - remaining months in guarantee period
def snapshot(val_date, av, issue_dt, gp_end, sc_tbl):
    year_num = policy_year(issue_dt, val_date)
    rate = sc_rate(sc_tbl, year_num)
    surrender_charge = av * rate
    mva = 0.0
    csv = av + mva - surrender_charge
    rem_months = month_diff(val_date, gp_end)

    return {
        "SurrenderChargeRate": rate,
        "SurrenderCharge": surrender_charge,
        "MVA": mva,
        "CashSurrenderValue": csv,
        "RemainingMonthsInGuaranteePeriod": rem_months,
    }


# Calculate the maturity date as the first policy anniversary
# on or after the annuitant reaches age 100.
def maturity_date_from_issue_and_annuitant(issue_dt, annuitant_dob):
    issue_dt = to_ts(issue_dt)
    annuitant_dob = to_ts(annuitant_dob)

    if pd.isna(issue_dt) or pd.isna(annuitant_dob):
        return pd.NaT

    age100 = safe_replace_year(annuitant_dob, annuitant_dob.year + 100)
    candidate = safe_replace_year(issue_dt, age100.year)
    if candidate < age100:
        candidate = safe_replace_year(issue_dt, age100.year + 1)

    return candidate


# Look at the policy input row and decide whether Event2 exists.
# For now, Event2 is created only when Gross WD is filled and not zero.
# If found, return a small event-input dictionary for Event2.
def extract_event2_input(row):
    gross_wd = pick_first(row, "Gross WD", "GrossWD")
    if not nonempty(gross_wd):
        return None

    if sfloat(gross_wd, 0.0) == 0.0:
        return None

    # Try several possible names for the second valuation date column.
    # If Excel had duplicate headers, pandas may rename it to "Valuation Date.1".
    event_date = pick_first(
        row,
        "Valuation Date.1",
        "ValuationDate.1",
        "Event2 Valuation Date",
        "Event2ValuationDate",
    )

    # As a backup, scan for any later valuation-date column created by pandas.
    if event_date is None:
        for col in row.index:
            col_str = str(col)
            if (col_str.startswith("Valuation Date.") or col_str.startswith("ValuationDate.")) and nonempty(row[col]):
                event_date = row[col]
                break

    return {
        "EventType": "PartialWithdrawal",
        "Valuation Date": event_date,
        "Gross WD": gross_wd,
        "Net": pick_first(row, "Net"),
        "Tax": pick_first(row, "Tax"),
    }


# Build Event1 / Initialization:
# 1. read raw input values
# 2. calculate derived values
# 3. validate the input
# 4. build the end-of-day state after Event1
def process_initialization(row, sc_tbl, product_tables):
    issue_dt = to_ts(pick_first(row, "IssueDate"))
    val_date = to_ts(pick_first(row, "Valuation Date", "ValuationDate"))
    annuitant = to_ts(pick_first(row, "AnnuitantDOB"))
    owner_dob = to_ts(pick_first(row, "OwnerDOB"))
    premium = sfloat(pick_first(row, "SinglePremium"))

    product_type = as_code(pick_first(row, "ProductType"))
    plan_code = as_code(pick_first(row, "PlanCode"))

    # Use ProductType or PlanCode to decide the guarantee term.
    # If neither is usable, default to 5-year.
    plan_key = product_type if product_type in PLAN_YEARS else plan_code
    if plan_key not in PLAN_YEARS:
        plan_key = "5"

    plan_years = PLAN_YEARS[plan_key]

    # Prefer SelectedRiders if already provided.
    # Otherwise combine Rider 1 / Rider 2 / Rider 3 into one text field.
    selected_riders = pick_first(row, "SelectedRiders")
    if not nonempty(selected_riders):
        selected_riders = ", ".join(
            str(row[c]).strip()
            for c in ("Rider 1", "Rider 2", "Rider 3")
            if c in row.index and nonempty(row[c])
        )

    # Use input guarantee dates if provided.
    # Otherwise derive them from issue date and plan years.
    gp_start = to_ts(pick_first(row, "GuaranteePeriodStartDate"))
    if pd.isna(gp_start):
        gp_start = issue_dt

    gp_end = to_ts(pick_first(row, "GuaranteePeriodEndDate"))
    if pd.isna(gp_end):
        gp_end = add_years(gp_start, plan_years)

    # Use input maturity date if provided.
    # Otherwise calculate it from issue date and annuitant date of birth.
    maturity_date = to_ts(pick_first(row, "MaturityDate"))
    if pd.isna(maturity_date):
        maturity_date = maturity_date_from_issue_and_annuitant(issue_dt, annuitant)

    # Lookup crediting rate and annual contract charge from product tables.
    rate_key = f"{plan_key}-year"
    lookup_ccr = to_pct(product_tables.get("CreditingRate", {}).get(rate_key, 0.0)) if product_tables else 0.0
    annual_contract_charge = 0.0 # Keep it simple for now;

    # Use input rate values if provided; otherwise use defaults / lookup values.
    gmir = to_pct(pick_first(row, "GuaranteedMinimumInterestRate"))
    if gmir is None:
        gmir = GMIR

    nonforf = to_pct(pick_first(row, "NonforfeitureRate"))
    if nonforf is None:
        nonforf = NONFORFEITURE

    premium_tax = to_pct(pick_first(row, "PremiumTaxRate"))
    if premium_tax is None:
        premium_tax = PREMIUM_TAX_RATE

    current_credit_rate = to_pct(pick_first(row, "CurrentCreditRate"))
    if current_credit_rate is None:
        current_credit_rate = lookup_ccr or 0.0

    mva_ref = to_pct(pick_first(row, "MVAReferenceRateAtStart"))
    if mva_ref is None:
        mva_ref = MVA_REF_RATE

    # Use input balances if provided.
    # If not, default AccountValue to premium and the others to zero.
    account_value = sfloat(pick_first(row, "AccountValue"), premium)
    acc_int = sfloat(pick_first(row, "AccumulatedInterestCurrentYear"), 0.0)
    pfwb = sfloat(pick_first(row, "PenaltyFreeWithdrawalBalance"), 0.0)

    # Raw Event1 input block.
    data = {
        "ValuationDate": val_date,
        "Event": "PolicyIssue",
        "PolicyNumber": pick_first(row, "PolicyNumber"),
        "IssueDate": issue_dt,
        "ProductType": pick_first(row, "ProductType"),
        "PlanCode": pick_first(row, "PlanCode"),
        "IssueAge": pick_first(row, "IssueAge"),
        "State": pick_first(row, "State"),
        "SinglePremium": premium,
        "SelectedRiders": selected_riders,
        "AnnuitantDOB": annuitant,
        "OwnerDOB": owner_dob,
        "AccountValue": account_value,
        "AccumulatedInterestCurrentYear": acc_int,
        "PenaltyFreeWithdrawalBalance": pfwb,
    }

    # Calculated Event1 block.
    calc = {
        "GuaranteedMinimumInterestRate": gmir,
        "NonforfeitureRate": nonforf,
        "MaturityDate": maturity_date,
        "PremiumTaxRate": premium_tax,
        "GuaranteePeriodStartDate": gp_start,
        "GuaranteePeriodEndDate": gp_end,
        "CurrentCreditRate": current_credit_rate,
        "MVAReferenceRateAtStart": mva_ref,
        "DailyInterest": 0.0,
        **snapshot(val_date, account_value, issue_dt, gp_end, sc_tbl),
    }

    # Validation block.
    # IssueDate outside the allowed range is a fatal error.
    # Other checks are warnings.
    validation = {}

    if pd.isna(issue_dt):
        validation["IssueDate"] = "E: IssueDate missing"
    elif not (pd.Timestamp("2020-01-01") <= issue_dt <= TODAY):
        validation["IssueDate"] = f"E: IssueDate outside [2020-01-01 ; {TODAY.date()}]"

    issue_age = sfloat(pick_first(row, "IssueAge"), None)
    if issue_age is not None and not (0 <= issue_age <= 95):
        validation["IssueAge"] = "W: IssueAge outside expected range [0 ; 95]"

    if premium < 10000 or premium > 1_000_000:
        validation["SinglePremium"] = "W: SinglePremium outside recommended range [10,000 ; 1,000,000]"

    # Stop the process if there is any fatal initialization error.
    if any(str(v).startswith("E:") for v in validation.values()):
        raise ValueError("Initialization errors:\n" + "\n".join(validation.values()))

    # Build the full end-of-day state after Event1.
    # Also store the annual contract charge in an internal helper field called _cc.
    eod = merge_state(
        data,
        calc,
        extras={
            "GrossWD": None,
            "Net": None,
            "Tax": None,
            "_cc": annual_contract_charge,
        },
    )

    return data, calc, validation, eod


# Build the next day's valuation from the previous end-of-day state.
# This step does not reread the original input row.
def roll_forward(prior_eod, sc_tbl, target_date=None):
    prior_date = to_ts(prior_eod["ValuationDate"])
    new_date = to_ts(target_date) if target_date is not None else prior_date + pd.Timedelta(days=1)

    # If the target date is blank or invalid, default to the next calendar day.
    if pd.isna(new_date):
        new_date = prior_date + pd.Timedelta(days=1)

    day_count = max((new_date - prior_date).days, 0)

    issue_dt = to_ts(prior_eod["IssueDate"])
    gp_end = to_ts(prior_eod["GuaranteePeriodEndDate"])
    ccr = sfloat(prior_eod["CurrentCreditRate"])
    prior_av = sfloat(prior_eod["AccountValue"])
    annual_cc = 0.0

    # Grow account value using crediting rate, then subtract daily contract charge.
    growth = (1 + ccr) ** (day_count / 365) if day_count > 0 else 1.0
    av_before_charge = prior_av * growth
    new_av = av_before_charge - annual_cc * (day_count / 365)

    # Reset AccumulatedInterestCurrentYear on the policy anniversary.
    anniversary = safe_replace_year(issue_dt, new_date.year)
    period_interest = new_av - prior_av

    if not pd.isna(anniversary) and new_date.date() == anniversary.date():
        acc_int = period_interest
    else:
        acc_int = sfloat(prior_eod.get("AccumulatedInterestCurrentYear"), 0.0) + period_interest

    # Carry forward the static fields from the previous EOD,
    # then overwrite the fields that change on valuation.
    valuation_state = {field: prior_eod.get(field) for field in STATIC_CARRY}
    valuation_state.update({
        "ValuationDate": new_date,
        "Event": "Valuation",
        "AccountValue": new_av,
        "DailyInterest": period_interest,
        "AccumulatedInterestCurrentYear": acc_int,
        **snapshot(new_date, new_av, issue_dt, gp_end, sc_tbl),
        "GrossWD": None,
        "Net": None,
        "Tax": None,
        "_cc": annual_cc,
    })

    return valuation_state


# Apply Event2 as a partial withdrawal.
# This reads the event input, checks it, updates balances,
# and builds the new end-of-day state after the event.
def process_withdrawal(val_state, event_input, sc_tbl):
    event_date_raw = event_input.get("Valuation Date")
    event_date = to_ts(event_date_raw) if nonempty(event_date_raw) else to_ts(val_state["ValuationDate"])

    gross_wd = sfloat(event_input.get("Gross WD"))
    net = sfloat(event_input.get("Net"), None) if nonempty(event_input.get("Net")) else None
    tax = sfloat(event_input.get("Tax"), None) if nonempty(event_input.get("Tax")) else None

    # Raw Event2 input block.
    data = {
        "ValuationDate": event_date,
        "Event": "PartialWithdrawal",
        "GrossWD": gross_wd,
        "Net": net,
        "Tax": tax,
    }

    pre_av = sfloat(val_state["AccountValue"])
    pfwb = sfloat(val_state["PenaltyFreeWithdrawalBalance"], 0.0)
    post_av = pre_av - gross_wd

    # Calculated Event2 block after applying the withdrawal.
    calc = {
        "AccountValue": post_av,
        "PenaltyFreeWithdrawalBalance": max(0.0, pfwb - gross_wd),
        **snapshot(event_date, post_av, val_state["IssueDate"], val_state["GuaranteePeriodEndDate"], sc_tbl),
    }

    # Validation block for Event2.
    validation = {}
    if not nonempty(event_date_raw):
        validation["ValuationDate"] = "W: Event2 valuation date missing; defaulted to next valuation date"

    if gross_wd > pre_av:
        validation["GrossWD"] = f"E: GrossWD ({gross_wd:,.2f}) exceeds AccountValue ({pre_av:,.2f})"
    elif gross_wd > pfwb:
        validation["GrossWD"] = (
            f"W: GrossWD ({gross_wd:,.2f}) exceeds "
            f"PenaltyFreeWithdrawalBalance ({pfwb:,.2f})"
        )

    # Stop the process if the withdrawal creates a fatal error.
    if any(str(v).startswith("E:") for v in validation.values()):
        raise ValueError("Event2 errors:\n" + "\n".join(validation.values()))

    # Build the full end-of-day state after Event2.
    eod = merge_state(data, calc, base=val_state)

    return data, calc, validation, eod