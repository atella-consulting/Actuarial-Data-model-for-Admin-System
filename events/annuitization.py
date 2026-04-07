# annuitization.py
from __future__ import annotations

from typing import Any, Dict, Tuple

import pandas as pd

from models import EventOutput, ValidationResult
from utils import pick_first, sfloat, nonempty, merge_state
from calculations import lookup_product_table_rate


class AnnuityEngine:
    def __init__(
        self,
        reference_xls: pd.ExcelFile,
        mortality2012_sheet: str = "2012_Period_Table_IAM2012",
        projection_sheet: str = "Projection_Scale_G2",
    ) -> None:
        """
        Load annuitization reference tables from the shared reference workbook.

        Expected sheets:
          - 2012_Period_Table_IAM2012
          - Projection_Scale_G2
        """
        if mortality2012_sheet not in reference_xls.sheet_names:
            raise ValueError(
                f"Reference workbook is missing required annuitization sheet: "
                f"{mortality2012_sheet!r}"
            )
        if projection_sheet not in reference_xls.sheet_names:
            raise ValueError(
                f"Reference workbook is missing required annuitization sheet: "
                f"{projection_sheet!r}"
            )

        mortality = pd.read_excel(
            reference_xls,
            sheet_name=mortality2012_sheet,
            engine="openpyxl",
            index_col=0,
        )
        proj = pd.read_excel(
            reference_xls,
            sheet_name=projection_sheet,
            engine="openpyxl",
            index_col=0,
        )

        mortality2013 = mortality * (1 - proj)
        self.survival2013 = 1 - mortality2013

    def _set_annuitant(self, gender: str, age: int) -> Tuple[str, int]:
        """
        Accepted inputs:
        - M / Male
        - F / Female
        """
        raw = str(gender).strip().upper()

        if raw in {"M", "MALE"}:
            gender_norm = "Male"
        elif raw in {"F", "FEMALE"}:
            gender_norm = "Female"
        else:
            raise ValueError(f"Invalid gender: {gender!r}")

        return gender_norm, int(age)

    def single_life(self, gender: str, age: int, interest_pct: float) -> Tuple[float, float]:
        interest = interest_pct / 100.0
        survival = self.survival2013[gender].iloc[age:].to_numpy(copy=True)
        survival[0] = 1
        df = 1 / (1 + interest)

        annuity_factor = 0.0
        num = 0.0
        for i in range(len(survival)):
            p = survival[: i + 1].prod()
            annuity_factor += (df ** i) * p
            num += i * (df ** i) * p
        return annuity_factor, (num / annuity_factor if annuity_factor else 0.0)

    def joint_survivor(self, g1: str, a1: int, g2: str, a2: int, interest_pct: float) -> Tuple[float, float]:
        interest = interest_pct / 100.0
        s1 = self.survival2013[g1].iloc[a1:].to_numpy(copy=True)
        s2 = self.survival2013[g2].iloc[a2:].to_numpy(copy=True)
        s1[0] = 1
        s2[0] = 1
        df = 1 / (1 + interest)

        annuity_factor = 0.0
        num = 0.0
        for i in range(max(len(s1), len(s2))):
            p1 = s1[: i + 1].prod() if i < len(s1) else 0.0
            p2 = s2[: i + 1].prod() if i < len(s2) else 0.0
            p_last = p1 + p2 - p1 * p2
            annuity_factor += (df ** i) * p_last
            num += i * (df ** i) * p_last
        return annuity_factor, (num / annuity_factor if annuity_factor else 0.0)

    def joint_life(self, g1: str, a1: int, g2: str, a2: int, interest_pct: float) -> Tuple[float, float]:
        interest = interest_pct / 100.0
        s1 = self.survival2013[g1].iloc[a1:].to_numpy(copy=True)
        s2 = self.survival2013[g2].iloc[a2:].to_numpy(copy=True)
        s1[0] = 1
        s2[0] = 1
        df = 1 / (1 + interest)

        annuity_factor = 0.0
        num = 0.0
        for i in range(min(len(s1), len(s2))):
            p_both = s1[: i + 1].prod() * s2[: i + 1].prod()
            annuity_factor += (df ** i) * p_both
            num += i * (df ** i) * p_both
        return annuity_factor, (num / annuity_factor if annuity_factor else 0.0)

    def single_life_term_certain(self, gender: str, age: int, term: int, interest_pct: float) -> Tuple[float, float]:
        if term < 0:
            raise ValueError("TermCertain must be >= 0")
        if interest_pct == 0:
            raise ValueError("Interest rate cannot be 0 for current term-certain formula")

        interest = interest_pct / 100.0
        survival = self.survival2013[gender].iloc[age:].to_numpy(copy=True)
        survival[0] = 1
        df = 1 / (1 + interest)

        term_component = (1 - df ** term) / interest
        survive_term = survival[: term + 1].prod()

        future_factor, _ = self.single_life(gender, age + term, interest_pct)
        annuity_factor = term_component + (df ** term) * survive_term * future_factor

        num = 0.0
        denom = 0.0
        for i in range(term + 1):
            num += i * (df ** i)
            denom += (df ** i)
        for i in range(term + 1, len(survival)):
            p = survival[:i].prod()
            num += i * (df ** i) * p
            denom += (df ** i) * p

        return annuity_factor, (num / denom if denom else 0.0)


def process_annuitization(
    row: pd.Series,
    base_state: Dict[str, Any],
    engine: AnnuityEngine,
    product_tables: pd.DataFrame,
) -> EventOutput:
    result = ValidationResult()

    annuity_type_raw = pick_first(row, "AnnuityType")
    annuity_type = str(annuity_type_raw or "").strip().lower()

    primary_age_raw = pick_first(row, "Primary_IssueAge", "IssueAge")
    primary_sex = pick_first(row, "Primary_Sex")

    secondary_age_raw = pick_first(row, "Secondary_IssueAge")
    secondary_sex = pick_first(row, "Secondary_Sex")

    term_raw = pick_first(row, "TermCertain")

    primary_age = int(sfloat(primary_age_raw, -1))
    secondary_age = int(sfloat(secondary_age_raw, -1)) if nonempty(secondary_age_raw) else None
    term = int(sfloat(term_raw, -1)) if nonempty(term_raw) else None

    annuity_input_fields = {
        "ValuationDate": base_state.get("ValuationDate"),
        "Event": "Annuitization",
        "Primary_IssueAge": primary_age_raw,
        "Primary_Sex": primary_sex,
        "Secondary_IssueAge": secondary_age_raw,
        "Secondary_Sex": secondary_sex,
        "TermCertain": term_raw,
        "AnnuityType": annuity_type_raw,
    }

    annuity_rate_dec = lookup_product_table_rate(
        product_tables=product_tables,
        table_name="AnnuityRate",
        product_type=base_state.get("ProductType"),
        valuation_date=base_state.get("ValuationDate"),
    )
    interest_pct = annuity_rate_dec * 100.0 if annuity_rate_dec is not None else None

    if not annuity_type:
        result.add_error("AnnuityType", "AnnuityType is required")

    if interest_pct is None:
        result.add_error(
            "AnnuityRate",
            "No ProductTables match found for AnnuityRate / "
            f"{base_state.get('ProductType')} / <= {base_state.get('ValuationDate')}"
        )

    if not nonempty(primary_sex) or primary_age < 0:
        result.add_error("PrimaryAnnuitant", "Primary_Sex and Primary_IssueAge are required")

    if annuity_type in {"joint_survivor", "joint_life"}:
        if not nonempty(secondary_sex) or secondary_age is None or secondary_age < 0:
            result.add_error("SecondaryAnnuitant", "Secondary_Sex and Secondary_IssueAge are required")

    if annuity_type == "single_term_certain":
        if term is None or term < 0:
            result.add_error("TermCertain", "TermCertain must be a non-negative integer")

    if result.has_errors():
        return EventOutput(
            event_type="Annuitization",
            data=annuity_input_fields,
            calc={},
            validation=result,
            eod=merge_state(annuity_input_fields, base=base_state),
        )

    p_sex, p_age = engine._set_annuitant(str(primary_sex), primary_age)

    if annuity_type == "single_life":
        pv, dur = engine.single_life(p_sex, p_age, interest_pct)
    elif annuity_type == "joint_survivor":
        s_sex, s_age = engine._set_annuitant(str(secondary_sex), int(secondary_age))
        pv, dur = engine.joint_survivor(p_sex, p_age, s_sex, s_age, interest_pct)
    elif annuity_type == "joint_life":
        s_sex, s_age = engine._set_annuitant(str(secondary_sex), int(secondary_age))
        pv, dur = engine.joint_life(p_sex, p_age, s_sex, s_age, interest_pct)
    elif annuity_type == "single_term_certain":
        pv, dur = engine.single_life_term_certain(p_sex, p_age, int(term), interest_pct)
    else:
        result.add_error("AnnuityType", f"Unsupported AnnuityType: {annuity_type}")
        return EventOutput(
            event_type="Annuitization",
            data=annuity_input_fields,
            calc={},
            validation=result,
            eod=merge_state(annuity_input_fields, base=base_state),
        )

    purchase_rate_per_1000 = 1000.0 / pv
    modal_benefit = purchase_rate_per_1000 * 100

    data = {
        **annuity_input_fields,
        "PV Expected Benefits": pv,
    }

    calc = {
        "PV Expected Benefits": pv,
        "Purchase Rate per $1,000": purchase_rate_per_1000,
        "Modal Benefit @ Issue": modal_benefit,
        "_annuity_duration": dur,
        "_annuity_type": annuity_type,
        "_annuity_interest_rate": annuity_rate_dec,
    }

    eod = merge_state(data, calc, base=base_state)

    return EventOutput(
        event_type="Annuitization",
        data=data,
        calc=calc,
        validation=result,
        eod=eod,
    )