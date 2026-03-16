"""
models.py
---------
Typed object model for the MYGA/FIA actuarial engine.

Core domain objects
-------------------
  Policy           - static contract fields set at issue
  AccountState     - mutable balance and derived fields at a valuation date
  Transaction      - fields specific to one event (e.g. a withdrawal)
  ValidationResult - accumulates field-level E:/W: messages
  EventOutput      - structured container returned by every event processor

All dataclass fields carry defaults so objects can be built incrementally.
Each class exposes to_dict() / from_dict() to stay compatible with the
dict-based engine internals and the Excel output writer.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

@dataclass
class Policy:
    """
    Static fields that identify and describe the insurance contract.

    Values are set at policy issue and are not expected to change during
    the guarantee period (except via an endorsement event, which is a
    future extension).
    """

    PolicyNumber: Optional[str] = None
    IssueDate: Optional[pd.Timestamp] = None
    ProductType: Optional[str] = None
    PlanCode: Optional[str] = None
    IssueAge: Optional[float] = None
    State: Optional[str] = None
    SinglePremium: float = 0.0
    SelectedRiders: Optional[str] = None

    # Dates
    AnnuitantDOB: Optional[pd.Timestamp] = None
    OwnerDOB: Optional[pd.Timestamp] = None
    MaturityDate: Optional[pd.Timestamp] = None
    GuaranteePeriodStartDate: Optional[pd.Timestamp] = None
    GuaranteePeriodEndDate: Optional[pd.Timestamp] = None

    # Rates (stored as decimals, e.g. 0.0575 for 5.75 %)
    GuaranteedMinimumInterestRate: Optional[float] = None
    NonforfeitureRate: Optional[float] = None
    PremiumTaxRate: float = 0.0
    CurrentCreditRate: float = 0.0
    MVAReferenceRateAtStart: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dict, omitting keys whose value is None."""
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Policy":
        """Construct from a plain dict, ignoring unrecognised keys."""
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# AccountState
# ---------------------------------------------------------------------------

@dataclass
class AccountState:
    """
    Mutable balance and derived fields that are recalculated on every
    valuation date and after every event.

    A new AccountState is produced:
      - after process_initialization (Event 1 EOD)
      - after each roll_forward (daily valuation)
      - after each event processor (Event 2+ EOD)
    """

    ValuationDate: Optional[pd.Timestamp] = None
    Event: Optional[str] = None

    AccountValue: float = 0.0
    AccumulatedInterestCurrentYear: float = 0.0
    PenaltyFreeWithdrawalBalance: float = 0.0

    # Surrender charge
    SurrenderChargeRate: float = 0.0
    SurrenderCharge: float = 0.0

    # Market Value Adjustment
    MVA: float = 0.0

    # Derived totals
    CashSurrenderValue: float = 0.0
    RemainingMonthsInGuaranteePeriod: int = 0
    DailyInterest: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AccountState":
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------

@dataclass
class Transaction:
    """
    Fields specific to a single event, such as a partial withdrawal.

    A Transaction is immutable once the event is processed; its values are
    recorded in the audit trail but do not carry forward automatically into
    the next valuation state.
    """

    ValuationDate: Optional[pd.Timestamp] = None
    Event: Optional[str] = None
    GrossWD: Optional[float] = None
    Net: Optional[float] = None
    Tax: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dict, omitting None values."""
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Transaction":
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """
    Accumulates field-level validation messages for a single event.

    Message prefix convention
    -------------------------
    ``"E: ..."``  ->  fatal error   (event processing must stop)
    ``"W: ..."``  ->  warning       (processing continues with a note)

    Typical usage::

        result = ValidationResult()
        result.add_error("IssueDate", "IssueDate is missing")
        result.add_warning("SinglePremium", "Premium outside recommended range")

        if result.has_errors():
            raise ValueError(result.error_summary())
    """

    messages: Dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Adding messages
    # ------------------------------------------------------------------

    def add(self, field_name: str, message: str) -> None:
        """Add a raw message — caller must include the E:/W: prefix."""
        self.messages[field_name] = message

    def add_error(self, field_name: str, message: str) -> None:
        """Add a fatal error message (E: prefix applied automatically)."""
        self.messages[field_name] = f"E: {message}"

    def add_warning(self, field_name: str, message: str) -> None:
        """Add a non-fatal warning message (W: prefix applied automatically)."""
        self.messages[field_name] = f"W: {message}"

    # ------------------------------------------------------------------
    # Querying messages
    # ------------------------------------------------------------------

    def has_errors(self) -> bool:
        """True if at least one fatal error is present."""
        return any(v.startswith("E:") for v in self.messages.values())

    def has_warnings(self) -> bool:
        """True if at least one warning is present."""
        return any(v.startswith("W:") for v in self.messages.values())

    def errors(self) -> List[Tuple[str, str]]:
        """Return (field, message) pairs for fatal errors only."""
        return [(k, v) for k, v in self.messages.items() if v.startswith("E:")]

    def warnings(self) -> List[Tuple[str, str]]:
        """Return (field, message) pairs for warnings only."""
        return [(k, v) for k, v in self.messages.items() if v.startswith("W:")]

    def error_summary(self) -> str:
        """Multi-line string of all fatal error messages (for raising ValueError)."""
        return "\n".join(v for v in self.messages.values() if v.startswith("E:"))

    def to_dict(self) -> Dict[str, str]:
        """Plain dict copy — passed directly to write_model() column blocks."""
        return self.messages.copy()

    def __bool__(self) -> bool:
        """True if any messages (errors or warnings) are present."""
        return bool(self.messages)

    def __len__(self) -> int:
        return len(self.messages)


# ---------------------------------------------------------------------------
# EventOutput
# ---------------------------------------------------------------------------

@dataclass
class EventOutput:
    """
    Structured container returned by every event-processing function.

    Every event processor (event_1.process_initialization,
    event_2.process_withdrawal, …) returns one EventOutput so that
    the orchestrator can handle all results uniformly.

    Attributes
    ----------
    event_type : str
        Human-readable label, e.g. ``"PolicyIssue"`` or ``"PartialWithdrawal"``.
    data : dict
        Raw input values read from the policy row / event input block.
    calc : dict
        Derived / calculated values produced by the engine for this event.
    validation : ValidationResult
        All validation messages collected during event processing.
    eod : dict
        Full end-of-day state dictionary after this event.
        This dict is passed into the next ``roll_forward()`` call.
    """

    event_type: str
    data: Dict[str, Any] = field(default_factory=dict)
    calc: Dict[str, Any] = field(default_factory=dict)
    validation: ValidationResult = field(default_factory=ValidationResult)
    eod: Dict[str, Any] = field(default_factory=dict)

    def as_col_specs(self, date_label: str) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Convert this EventOutput into the ``(column_name, block)`` tuples
        expected by ``write_model()`` in Actuarial_Data_Model.py.

        Parameters
        ----------
        date_label : str
            Formatted valuation date string used in the EOD column header,
            e.g. ``"2026-02-01"``.

        Returns
        -------
        list of (str, dict) pairs — one per output column block.
        """
        num = _EVENT_NUMBER.get(self.event_type, "N")
        return [
            (f"Event{num} Data",             self.data),
            (f"Event{num} Calc",             self.calc),
            (f"Event{num} Validation",       self.validation.to_dict()),
            (f"EOD {date_label} / After Event{num}", self.eod),
        ]

    def raise_if_errors(self) -> None:
        """Raise ValueError with all fatal error messages, if any exist."""
        if self.validation.has_errors():
            raise ValueError(
                f"[{self.event_type}] fatal errors:\n"
                + self.validation.error_summary()
            )


# Map event type labels to the numeric suffix used in column headers.
# Add new event types here as the engine grows.
_EVENT_NUMBER: Dict[str, str] = {
    "PolicyIssue":       "1",
    "PartialWithdrawal": "2",
    "FullSurrender":     "3",
    "Annuitization":     "4",
    "Death":             "5",
}
