import os
import pandas as pd


def prompt_path(prompt, default):
    path = input(f"{prompt} [Default: {default}]: ").strip()
    path = path or default
    return os.path.abspath(path.strip('"').strip("'"))


def to_percent(x):
    if pd.isna(x):
        return None
    if isinstance(x, str):
        x = x.strip().replace(",", "")
        if x.endswith("%"):
            return float(x[:-1]) / 100
        x = float(x)
    x = float(x)
    return x / 100 if x > 1 else x


def to_short_date(x):
    if pd.isna(x):
        return ""
    return pd.to_datetime(x, errors="coerce").strftime("%Y-%m-%d")


def policy_year(issue_date, valuation_date):
    issue_date = pd.to_datetime(issue_date)
    valuation_date = pd.to_datetime(valuation_date)

    year_num = valuation_date.year - issue_date.year + 1
    anniversary = issue_date.replace(year=valuation_date.year)

    if valuation_date < anniversary:
        year_num -= 1

    return max(year_num, 1)


def month_diff(start_date, end_date):
    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date)

    months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
    if end_date.day < start_date.day:
        months -= 1
    return max(months, 0)


def get_surrender_charge_rate(sc_table, year_num):
    sc = sc_table.copy()
    sc.columns = [str(c).strip() for c in sc.columns]

    sc["Year"] = pd.to_numeric(sc["Year"], errors="coerce")
    sc["ChargeRate"] = sc["ChargeRate"].map(to_percent)

    match = sc.loc[sc["Year"] == year_num, "ChargeRate"]
    return float(match.iloc[0]) if not match.empty else 0.0


def build_output(policy_row, sc_table):
    row = policy_row.copy()

    # remove unwanted column if present
    row = row.drop(labels=["Unnamed: 27"], errors="ignore")

    # date fields
    date_fields = [
        "Valuation Date", "IssueDate", "MaturityDate", "AnnuitantDOB",
        "OwnerDOB", "GuaranteePeriodStartDate", "GuaranteePeriodEndDate"
    ]
    for c in date_fields:
        if c in row.index:
            row[c] = pd.to_datetime(row[c], errors="coerce")

    # percent fields
    for c in [
        "GuaranteedMinimumInterestRate",
        "NonforfeitureRate",
        "PremiumTaxRate",
        "CurrentCreditRate",
        "MVAReferenceRateAtStart",
    ]:
        if c in row.index:
            row[c] = to_percent(row[c])

    # numeric fields
    for c in [
        "SinglePremium",
        "AccumulatedInterestCurrentYear",
        "PenaltyFreeWithdrawalBalance",
        "RemainingMonthsInGuaranteePeriod",
        "AccountValue",
        "CashSurrenderValue",
        "Gross WD",
        "Net",
        "Tax",
    ]:
        if c in row.index:
            row[c] = pd.to_numeric(row[c], errors="coerce")

    prior_av = float(row.get("AccountValue", 0) or 0)
    gross_wd = float(row.get("Gross WD", 0) or 0)
    net = float(row.get("Net", 0) or 0) if pd.notna(row.get("Net", 0)) else ""
    tax = float(row.get("Tax", 0) or 0) if pd.notna(row.get("Tax", 0)) else ""

    next_val_date = row["Valuation Date"] + pd.Timedelta(days=1)
    growth = (1 + row["CurrentCreditRate"]) ** (1 / 365)

    av_before = prior_av * growth
    av_after = av_before - gross_wd
    daily_interest = av_before - prior_av
    acc_int = float(row.get("AccumulatedInterestCurrentYear", 0) or 0) + daily_interest

    remaining_months = month_diff(next_val_date, row["GuaranteePeriodEndDate"])

    year_num = policy_year(row["IssueDate"], next_val_date)
    sc_rate = get_surrender_charge_rate(sc_table, year_num)

    mva_before = 0.0
    mva_after = 0.0

    surrender_charge_before = av_before * sc_rate
    surrender_charge_after = av_after * sc_rate

    csv_before = av_before + mva_before - surrender_charge_before
    csv_after = av_after + mva_after - surrender_charge_after

    fields = [
        ("Valuation Date", to_short_date(next_val_date), to_short_date(next_val_date)),
        ("PolicyNumber", row.get("PolicyNumber", ""), row.get("PolicyNumber", "")),
        ("IssueDate", to_short_date(row.get("IssueDate", "")), to_short_date(row.get("IssueDate", ""))),
        ("ProductType", row.get("ProductType", ""), row.get("ProductType", "")),
        ("PlanCode", row.get("PlanCode", ""), row.get("PlanCode", "")),
        ("IssueAge", row.get("IssueAge", ""), row.get("IssueAge", "")),
        ("State", row.get("State", ""), row.get("State", "")),
        ("SinglePremium", row.get("SinglePremium", ""), row.get("SinglePremium", "")),
        ("SelectedRiders", row.get("SelectedRiders", ""), row.get("SelectedRiders", "")),
        ("GuaranteedMinimumInterestRate", row.get("GuaranteedMinimumInterestRate", ""), row.get("GuaranteedMinimumInterestRate", "")),
        ("NonforfeitureRate", row.get("NonforfeitureRate", ""), row.get("NonforfeitureRate", "")),
        ("MaturityDate", to_short_date(row.get("MaturityDate", "")), to_short_date(row.get("MaturityDate", ""))),
        ("AnnuitantDOB", to_short_date(row.get("AnnuitantDOB", "")), to_short_date(row.get("AnnuitantDOB", ""))),
        ("OwnerDOB", to_short_date(row.get("OwnerDOB", "")), to_short_date(row.get("OwnerDOB", ""))),
        ("PremiumTaxRate", row.get("PremiumTaxRate", ""), row.get("PremiumTaxRate", "")),
        ("GuaranteePeriodStartDate", to_short_date(row.get("GuaranteePeriodStartDate", "")), to_short_date(row.get("GuaranteePeriodStartDate", ""))),
        ("GuaranteePeriodEndDate", to_short_date(row.get("GuaranteePeriodEndDate", "")), to_short_date(row.get("GuaranteePeriodEndDate", ""))),
        ("CurrentCreditRate", row.get("CurrentCreditRate", ""), row.get("CurrentCreditRate", "")),
        ("MVAReferenceRateAtStart", row.get("MVAReferenceRateAtStart", ""), row.get("MVAReferenceRateAtStart", "")),
        ("PenaltyFreeWithdrawalBalance", row.get("PenaltyFreeWithdrawalBalance", ""), row.get("PenaltyFreeWithdrawalBalance", "")),
        ("RemainingMonthsInGuaranteePeriod", remaining_months, remaining_months),
        ("AccountValue", av_before, av_after),
        ("SurrenderChargeRate", sc_rate, sc_rate),
        ("SurrenderCharge", surrender_charge_before, surrender_charge_after),
        ("MVA", mva_before, mva_after),
        ("CashSurrenderValue", csv_before, csv_after),
        ("Rider 1", "", ""),
        ("Rider 2", "", ""),
        ("Rider 3", "", ""),
        ("DailyInterest", daily_interest, daily_interest),
        ("AccumulatedInterestCurrentYear", acc_int, acc_int),
        ("TransactionHistory", "", ""),
        ("Gross WD", "", gross_wd if gross_wd != 0 else ""),
        ("Net", "", net if net != 0 else ""),
        ("Tax", "", tax if tax != 0 else ""),
    ]

    return pd.DataFrame(fields, columns=["Field", "Before Transaction", "After Transaction"])


def main():
    print("\nActuarial-Data-model-for-Admin-System")

    input_path = prompt_path("Enter input Excel file", "policy_input.xlsx")
    output_path = prompt_path("Enter output Excel file", "policy_output_next_day.xlsx")

    policy_df = pd.read_excel(input_path, engine="openpyxl")
    sc_table = pd.read_excel(
        input_path,
        sheet_name="SurrenderCharges",
        skiprows=1,
        engine="openpyxl"
    )

    if policy_df.empty:
        raise ValueError("Input file has no policy data.")

    result = build_output(policy_df.iloc[0], sc_table)
    result.to_excel(output_path, index=False, engine="openpyxl")

    print(f"\nDone. Output saved to: {output_path}")


if __name__ == "__main__":
    main()