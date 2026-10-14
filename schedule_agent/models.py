from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


@dataclass(slots=True)
class Project:
    project_id: str
    project_name: str
    start_date: date
    contract_finish: date
    data_date: date
    calendar_id: str
    current_finish: date | None = None
    preparer: str = ""
    manager: str = ""


@dataclass(slots=True)
class CalendarSpec:
    calendar_id: str
    work_weekdays: set[int]
    holidays: set[date] = field(default_factory=set)
    extra_workdays: set[date] = field(default_factory=set)


@dataclass(slots=True)
class Task:
    wbs: str
    name: str
    discipline: str
    section: str
    station: str
    workfront: str
    unit: str
    quantity: float
    status: str
    confirmed: bool
    duration_override: int | None = None
    is_milestone: bool = False


@dataclass(slots=True)
class Relation:
    predecessor: str
    successor: str
    relation_type: str
    lag: int = 0
    confirmed: bool = True


@dataclass(slots=True)
class Resource:
    wbs: str
    resource_type: str
    current_qty: float
    hard_cap: float
    extra_available: float
    unit_productivity: float
    source: str
    applicable_condition: str
    confirmed: bool


@dataclass(slots=True)
class CrashParam:
    wbs: str
    max_resource: float
    min_duration: int
    efficiency: float
    overtime_condition: str
    approval_required: str
    confirmed: bool


@dataclass(slots=True)
class ExternalConstraint:
    constraint_id: str
    wbs: str
    category: str
    not_before: date
    owner: str
    status: str
    confirmed: bool


@dataclass(slots=True)
class ActualProgress:
    wbs: str
    actual_quantity: float
    actual_start: date | None
    actual_finish: date | None
    remaining_duration: int | None
    evidence_ref: str
    reporter: str


@dataclass(slots=True)
class DelayCause:
    wbs: str
    category: str
    note: str
    responsibility: str
    controllable: str


@dataclass(slots=True)
class PeriodPlan:
    wbs: str
    period_end: date
    planned_quantity: float


@dataclass(slots=True)
class BaselineRecord:
    wbs: str
    contract_start: date | None
    contract_finish: date | None
    current_start: date | None
    current_finish: date | None


@dataclass(slots=True)
class Milestone:
    milestone_id: str
    name: str
    linked_wbs: str
    contract_date: date
    current_date: date | None


@dataclass(slots=True)
class SourceRef:
    entity_type: str
    entity_id: str
    field_name: str
    sheet: str
    cell: str
    raw_value: Any


@dataclass(slots=True)
class InputBundle:
    project: Project
    calendar: CalendarSpec
    tasks: list[Task]
    relations: list[Relation]
    resources: list[Resource]
    crash_params: list[CrashParam]
    constraints: list[ExternalConstraint]
    actuals: list[ActualProgress]
    delay_causes: list[DelayCause]
    period_plans: list[PeriodPlan]
    baselines: list[BaselineRecord]
    milestones: list[Milestone]
    source_refs: list[SourceRef]
    input_hash: str
    input_filename: str
    import_mode: str = "标准模板"
    import_notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Issue:
    severity: str
    code: str
    message: str
    entity_id: str = ""
    field_name: str = ""
    blocking: bool = False


@dataclass(slots=True)
class ScheduleRow:
    wbs: str
    name: str
    duration: int
    early_start: date
    early_finish: date
    late_start: date
    late_finish: date
    total_float: int
    free_float: int
    critical: bool
    section: str
    workfront: str


@dataclass(slots=True)
class ForecastRow:
    wbs: str
    status: str
    planned_percent: float
    actual_percent: float
    variance_pp: float
    baseline_start: date
    baseline_finish: date
    contract_baseline_finish: date | None
    forecast_start: date
    forecast_finish: date
    finish_variance_days: int
    contract_variance_days: int | None
    remaining_duration: int
    current_critical: bool
    alert: bool
    alert_reason: str
    efficiency_remaining: int | None


@dataclass(slots=True)
class Conflict:
    conflict_type: str
    key: str
    conflict_date: date
    demand: float
    capacity: float
    tasks: list[str]
    message: str


@dataclass(slots=True)
class Scenario:
    scenario_id: str
    action_type: str
    task_wbs: str
    task_name: str
    before_duration: int
    after_duration: int | None
    resource_delta: float | None
    recovered_days: int | None
    milestone_impact: str
    resource_conflict: str
    risk: str
    approval: str
    quantified: bool


@dataclass(slots=True)
class AnalysisResult:
    run_id: str
    version: int
    state: str
    formal_eligible: bool
    rule_version: str
    calculated_at: str
    input_filename: str
    input_hash: str
    project: Project
    issues: list[Issue]
    schedule: list[ScheduleRow]
    forecast: list[ForecastRow]
    conflicts: list[Conflict]
    scenarios: list[Scenario]
    milestones: list[dict[str, Any]]
    traceability: list[dict[str, Any]]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


def _serialize(value: Any) -> Any:
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize(item) for item in value]
    return value
