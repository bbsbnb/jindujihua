from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Callable

from .calendar import WorkCalendar
from .cpm import NetworkError, NetworkResult, calculate_cpm
from .models import (
    AnalysisResult,
    Conflict,
    ForecastRow,
    InputBundle,
    Issue,
    Scenario,
    ScheduleRow,
    Task,
)
from .validator import validate_bundle


RULE_VERSION = "1.0.0"


def calculate_durations(bundle: InputBundle) -> dict[str, int]:
    resources = defaultdict(list)
    for resource in bundle.resources:
        resources[resource.wbs].append(resource)
    durations: dict[str, int] = {}
    for task in bundle.tasks:
        if task.is_milestone:
            durations[task.wbs] = 1
        elif task.duration_override is not None and task.duration_override > 0:
            durations[task.wbs] = task.duration_override
        else:
            total_daily_output = sum(item.current_qty * item.unit_productivity for item in resources[task.wbs])
            durations[task.wbs] = max(1, math.ceil(task.quantity / total_daily_output)) if total_daily_output > 0 else 1
    return durations


def analyze_bundle(bundle: InputBundle, run_id: str, version: int) -> AnalysisResult:
    calendar = WorkCalendar(bundle.calendar)
    durations = calculate_durations(bundle)
    issues = validate_bundle(bundle, durations)
    schedule: list[ScheduleRow] = []
    baseline_result: NetworkResult | None = None
    try:
        baseline_result = calculate_cpm(
            bundle.tasks,
            bundle.relations,
            durations,
            calendar,
            bundle.project.start_date,
            _planning_bounds(bundle),
        )
        schedule = baseline_result.rows
    except NetworkError:
        pass

    forecast, forecast_result, forecast_issues = _calculate_forecast(bundle, schedule, durations, calendar)
    issues.extend(forecast_issues)
    conflicts = detect_conflicts(bundle, schedule, calendar)
    scenarios = build_scenarios(bundle, forecast, durations, calendar, forecast_result)
    milestones = evaluate_milestones(bundle, schedule, forecast)
    blocking = any(issue.blocking for issue in issues)
    formal_eligible = bool(schedule) and not blocking
    calculated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    traceability = build_traceability(bundle, schedule, forecast, scenarios, calculated_at)

    project_forecast_finish = max((row.forecast_finish for row in forecast), default=None)
    baseline_finish = baseline_result.project_finish if baseline_result else None
    critical_count = sum(row.critical for row in schedule)
    alert_count = sum(row.alert for row in forecast)
    project_variance_current = (
        calendar.workday_distance(bundle.project.current_finish or baseline_finish, project_forecast_finish)
        if project_forecast_finish and (bundle.project.current_finish or baseline_finish)
        else None
    )
    project_variance_contract = (
        calendar.workday_distance(bundle.project.contract_finish, project_forecast_finish)
        if project_forecast_finish
        else None
    )
    summary = {
        "import_mode": bundle.import_mode,
        "task_count": len(bundle.tasks),
        "critical_count": critical_count,
        "alert_count": alert_count,
        "blocking_count": sum(issue.blocking for issue in issues),
        "warning_count": sum(issue.severity == "警告" for issue in issues),
        "baseline_finish": baseline_finish,
        "forecast_finish": project_forecast_finish,
        "contract_finish": bundle.project.contract_finish,
        "current_approved_finish": bundle.project.current_finish or baseline_finish,
        "variance_current_days": project_variance_current,
        "variance_contract_days": project_variance_contract,
        "critical_path": [row.wbs for row in schedule if row.critical],
    }
    return AnalysisResult(
        run_id=run_id,
        version=version,
        state="草案",
        formal_eligible=formal_eligible,
        rule_version=RULE_VERSION,
        calculated_at=calculated_at,
        input_filename=bundle.input_filename,
        input_hash=bundle.input_hash,
        project=bundle.project,
        issues=issues,
        schedule=schedule,
        forecast=forecast,
        conflicts=conflicts,
        scenarios=scenarios,
        milestones=milestones,
        traceability=traceability,
        summary=summary,
    )


def _constraint_bounds(bundle: InputBundle) -> dict[str, date]:
    result: dict[str, date] = {}
    for item in bundle.constraints:
        current = result.get(item.wbs)
        if current is None or item.not_before > current:
            result[item.wbs] = item.not_before
    return result


def _planning_bounds(bundle: InputBundle) -> dict[str, date]:
    result = _constraint_bounds(bundle)
    for item in bundle.baselines:
        if item.current_start:
            current = result.get(item.wbs)
            if current is None or item.current_start > current:
                result[item.wbs] = item.current_start
    return result


def _calculate_forecast(
    bundle: InputBundle,
    baseline: list[ScheduleRow],
    durations: dict[str, int],
    calendar: WorkCalendar,
) -> tuple[list[ForecastRow], NetworkResult | None, list[Issue]]:
    issues: list[Issue] = []
    baseline_by_wbs = {row.wbs: row for row in baseline}
    imported_baselines = {row.wbs: row for row in bundle.baselines}
    actual_by_wbs = {item.wbs: item for item in bundle.actuals}
    period_by_wbs = defaultdict(list)
    for item in bundle.period_plans:
        period_by_wbs[item.wbs].append(item)
    remaining_tasks: list[Task] = []
    remaining_durations: dict[str, int] = {}
    for task in bundle.tasks:
        if task.status == "已完成":
            continue
        actual = actual_by_wbs.get(task.wbs)
        remaining_tasks.append(task)
        if task.status == "进行中" and actual and actual.remaining_duration:
            remaining_durations[task.wbs] = actual.remaining_duration
        else:
            remaining_durations[task.wbs] = durations[task.wbs]

    active_ids = {task.wbs for task in remaining_tasks}
    relations = [
        relation
        for relation in bundle.relations
        if relation.predecessor in active_ids and relation.successor in active_ids
    ]
    next_status_day = calendar.next_workday(bundle.project.data_date, inclusive=False)
    bounds = {wbs: max(value, next_status_day) for wbs, value in _planning_bounds(bundle).items() if wbs in active_ids}
    for task in remaining_tasks:
        bounds.setdefault(task.wbs, next_status_day)

    forecast_result: NetworkResult | None = None
    forecast_schedule: dict[str, ScheduleRow] = {}
    if remaining_tasks:
        try:
            forecast_result = calculate_cpm(
                remaining_tasks,
                relations,
                remaining_durations,
                calendar,
                next_status_day,
                bounds,
            )
            forecast_schedule = {row.wbs: row for row in forecast_result.rows}
        except NetworkError as exc:
            issues.append(Issue("错误", "FORECAST_NETWORK_ERROR", str(exc), blocking=True))

    rows: list[ForecastRow] = []
    for task in bundle.tasks:
        baseline_row = baseline_by_wbs.get(task.wbs)
        if baseline_row is None:
            continue
        actual = actual_by_wbs.get(task.wbs)
        imported = imported_baselines.get(task.wbs)
        current_start = imported.current_start if imported and imported.current_start else baseline_row.early_start
        current_finish = imported.current_finish if imported and imported.current_finish else baseline_row.early_finish
        contract_finish = imported.contract_finish if imported else None
        planned_row = ScheduleRow(
            wbs=baseline_row.wbs,
            name=baseline_row.name,
            duration=max(1, calendar.working_days_inclusive(current_start, current_finish)),
            early_start=current_start,
            early_finish=current_finish,
            late_start=current_start,
            late_finish=current_finish,
            total_float=baseline_row.total_float,
            free_float=baseline_row.free_float,
            critical=baseline_row.critical,
            section=baseline_row.section,
            workfront=baseline_row.workfront,
        )
        planned_percent = _planned_percent(task, planned_row, bundle.project.data_date, calendar, period_by_wbs[task.wbs])
        actual_percent = min(100.0, max(0.0, 100.0 * (actual.actual_quantity if actual else 0.0) / task.quantity)) if task.quantity else (100.0 if task.status == "已完成" else 0.0)
        variance_pp = round(actual_percent - planned_percent, 2)
        efficiency_remaining = _efficiency_remaining(task, actual, bundle.project.data_date, calendar)
        if task.status == "已完成" and actual and actual.actual_finish:
            forecast_start = actual.actual_start or current_start
            forecast_finish = actual.actual_finish
            remaining_duration = 0
            current_critical = False
        else:
            row = forecast_schedule.get(task.wbs, baseline_row)
            forecast_start = actual.actual_start if task.status == "进行中" and actual and actual.actual_start else row.early_start
            forecast_finish = row.early_finish
            remaining_duration = remaining_durations.get(task.wbs, 0)
            current_critical = row.critical
        finish_variance = calendar.workday_distance(current_finish, forecast_finish)
        contract_variance = calendar.workday_distance(contract_finish, forecast_finish) if contract_finish else None
        reasons: list[str] = []
        if current_critical and task.status != "已完成" and (variance_pp < 0 or finish_variance > 0):
            reasons.append("关键任务异常")
        if variance_pp <= -10:
            reasons.append("完成率落后不少于10个百分点")
        if finish_variance >= 5:
            reasons.append("完成日期偏差不少于5个工作日")
        rows.append(
            ForecastRow(
                wbs=task.wbs,
                status=task.status,
                planned_percent=round(planned_percent, 2),
                actual_percent=round(actual_percent, 2),
                variance_pp=variance_pp,
                baseline_start=current_start,
                baseline_finish=current_finish,
                contract_baseline_finish=contract_finish,
                forecast_start=forecast_start,
                forecast_finish=forecast_finish,
                finish_variance_days=finish_variance,
                contract_variance_days=contract_variance,
                remaining_duration=remaining_duration,
                current_critical=current_critical,
                alert=bool(reasons),
                alert_reason="；".join(reasons),
                efficiency_remaining=efficiency_remaining,
            )
        )
    return rows, forecast_result, issues


def _planned_percent(task: Task, row: ScheduleRow, data_date: date, calendar: WorkCalendar, periods: list) -> float:
    relevant = [item for item in periods if item.period_end <= data_date]
    if relevant and task.quantity:
        return min(100.0, 100.0 * sum(item.planned_quantity for item in relevant) / task.quantity)
    if data_date < row.early_start:
        return 0.0
    if data_date >= row.early_finish:
        return 100.0
    elapsed = calendar.working_days_inclusive(row.early_start, data_date)
    return min(100.0, 100.0 * elapsed / row.duration)


def _efficiency_remaining(task: Task, actual, data_date: date, calendar: WorkCalendar) -> int | None:
    if not actual or not actual.actual_start or actual.actual_quantity <= 0 or actual.actual_quantity >= task.quantity:
        return None
    elapsed = calendar.working_days_inclusive(actual.actual_start, data_date)
    if elapsed <= 0:
        return None
    daily = actual.actual_quantity / elapsed
    return max(1, math.ceil((task.quantity - actual.actual_quantity) / daily)) if daily > 0 else None


def detect_conflicts(
    bundle: InputBundle,
    schedule: list[ScheduleRow],
    calendar: WorkCalendar,
    quantity_overrides: dict[tuple[str, str], float] | None = None,
) -> list[Conflict]:
    conflicts: list[Conflict] = []
    quantity_overrides = quantity_overrides or {}
    schedule_by_wbs = {row.wbs: row for row in schedule}
    resource_usage: dict[tuple[str, date], list[tuple[str, float, float]]] = defaultdict(list)
    for resource in bundle.resources:
        row = schedule_by_wbs.get(resource.wbs)
        if not row:
            continue
        quantity = quantity_overrides.get((resource.wbs, resource.resource_type), resource.current_qty)
        for day in calendar.iter_workdays(row.early_start, row.early_finish):
            resource_usage[(resource.resource_type, day)].append((resource.wbs, quantity, resource.hard_cap))
    for (resource_type, day), usage in resource_usage.items():
        demand = sum(item[1] for item in usage)
        capacity = max(item[2] for item in usage)
        if demand > capacity:
            tasks = sorted((str(item[0]) for item in usage), key=str)
            conflicts.append(Conflict("资源", resource_type, day, demand, capacity, tasks, f"{resource_type}需求 {demand:g} 超过硬上限 {capacity:g}"))

    workfront_usage: dict[tuple[str, str, date], list[str]] = defaultdict(list)
    for row in schedule:
        if not row.workfront:
            continue
        for day in calendar.iter_workdays(row.early_start, row.early_finish):
            workfront_usage[(row.section, row.workfront, day)].append(row.wbs)
    for (section, workfront, day), tasks in workfront_usage.items():
        if len(tasks) > 1:
            conflicts.append(Conflict("工作面", f"{section}/{workfront}", day, len(tasks), 1, sorted((str(item) for item in tasks), key=str), "同一工作面存在并行施工"))
    return conflicts


def build_scenarios(
    bundle: InputBundle,
    forecast: list[ForecastRow],
    durations: dict[str, int],
    calendar: WorkCalendar,
    forecast_result: NetworkResult | None,
) -> list[Scenario]:
    task_map = {task.wbs: task for task in bundle.tasks}
    resource_map = defaultdict(list)
    for item in bundle.resources:
        resource_map[item.wbs].append(item)
    crash_map = {item.wbs: item for item in bundle.crash_params if item.confirmed}
    candidates = [row for row in forecast if row.alert and row.current_critical and row.status != "已完成"]
    scenarios: list[Scenario] = []
    base_finish = forecast_result.project_finish if forecast_result else None
    active_tasks = [task for task in bundle.tasks if task.status != "已完成"]
    active_ids = {task.wbs for task in active_tasks}
    active_relations = [relation for relation in bundle.relations if relation.predecessor in active_ids and relation.successor in active_ids]
    base_durations = {row.wbs: row.remaining_duration for row in forecast if row.wbs in active_ids}
    forecast_start = calendar.next_workday(bundle.project.data_date, inclusive=False)
    bounds = {wbs: max(value, forecast_start) for wbs, value in _planning_bounds(bundle).items() if wbs in active_ids}
    for task in active_tasks:
        bounds.setdefault(task.wbs, forecast_start)
    for index, row in enumerate(candidates[:5], start=1):
        task = task_map[row.wbs]
        crash = crash_map.get(row.wbs)
        resources = resource_map[row.wbs]
        primary = resources[0] if resources else None
        if crash and primary and primary.current_qty > 0:
            allowed = min(crash.max_resource, primary.hard_cap, primary.current_qty + primary.extra_available)
            ratio = max(1.0, allowed / primary.current_qty)
            effective_factor = 1 + (ratio - 1) * max(0.0, crash.efficiency)
            after = max(crash.min_duration, math.ceil(row.remaining_duration / effective_factor))
            scenario_durations = dict(base_durations)
            scenario_durations[row.wbs] = after
            simulated = calculate_cpm(active_tasks, active_relations, scenario_durations, calendar, forecast_start, bounds)
            recovered = max(0, calendar.workday_distance(simulated.project_finish, base_finish)) if base_finish else 0
            conflicts = detect_conflicts(
                bundle,
                simulated.rows,
                calendar,
                {(row.wbs, primary.resource_type): allowed},
            )
            conflict_text = (
                f"模拟后仍有 {len(conflicts)} 项资源/工作面冲突，需逐项复核"
                if conflicts
                else "模拟排程未发现资源或工作面冲突"
            )
            scenarios.append(
                Scenario(
                    scenario_id=f"S{index:02d}",
                    action_type="增加资源",
                    task_wbs=row.wbs,
                    task_name=task.name,
                    before_duration=row.remaining_duration,
                    after_duration=after,
                    resource_delta=round(allowed - primary.current_qty, 2),
                    recovered_days=recovered,
                    milestone_impact=f"项目预测竣工由 {base_finish.isoformat()} 调整为 {simulated.project_finish.isoformat()}",
                    resource_conflict=conflict_text,
                    risk=(
                        "本任务缩短但项目完工无净收益，不建议优先；受工作面、配套机械和边际效率约束"
                        if recovered == 0
                        else "受工作面、配套机械和边际效率约束"
                    ),
                    approval=crash.approval_required or "项目经理批准",
                    quantified=True,
                )
            )
        else:
            scenarios.append(
                Scenario(
                    scenario_id=f"S{index:02d}",
                    action_type="资源重配/合理搭接/延长作业时间",
                    task_wbs=row.wbs,
                    task_name=task.name,
                    before_duration=row.remaining_duration,
                    after_duration=None,
                    resource_delta=None,
                    recovered_days=None,
                    milestone_impact="缺少已确认赶工参数，不能量化追回天数",
                    resource_conflict="需检查工作面及资源冲突",
                    risk="逻辑调整、加班和资源重配均需现场可行性复核",
                    approval="补充赶工参数并由项目经理批准",
                    quantified=False,
                )
            )
    return sorted(
        scenarios,
        key=lambda item: (
            item.recovered_days is None,
            -(item.recovered_days or 0),
            item.scenario_id,
        ),
    )


def evaluate_milestones(bundle: InputBundle, schedule: list[ScheduleRow], forecast: list[ForecastRow]) -> list[dict]:
    baseline = {row.wbs: row for row in schedule}
    predicted = {row.wbs: row for row in forecast}
    calendar = WorkCalendar(bundle.calendar)
    rows = []
    for item in bundle.milestones:
        base = baseline.get(item.linked_wbs)
        current = predicted.get(item.linked_wbs)
        forecast_date = current.forecast_finish if current else (base.early_finish if base else None)
        approved_date = item.current_date or item.contract_date
        rows.append({
            "milestone_id": item.milestone_id,
            "name": item.name,
            "linked_wbs": item.linked_wbs,
            "contract_date": item.contract_date,
            "current_date": approved_date,
            "forecast_date": forecast_date,
            "variance_current_days": calendar.workday_distance(approved_date, forecast_date) if forecast_date else None,
            "variance_contract_days": calendar.workday_distance(item.contract_date, forecast_date) if forecast_date else None,
            "alert": bool(forecast_date and forecast_date > approved_date),
        })
    return rows


def build_traceability(bundle: InputBundle, schedule, forecast, scenarios, calculated_at: str) -> list[dict]:
    refs = defaultdict(list)
    for item in bundle.source_refs:
        refs[(item.entity_type, item.entity_id, item.field_name)].append(item)
    rows: list[dict] = []

    def source_cells(entity: str, sheet_fields: list[tuple[str, str]]) -> str:
        matches = []
        for source in bundle.source_refs:
            if source.entity_id == entity and (source.sheet, source.field_name) in sheet_fields:
                matches.append(f"{source.sheet}!{source.cell}={source.raw_value}")
        if not matches and bundle.import_mode != "标准模板":
            for source in bundle.source_refs:
                if source.entity_id == entity:
                    matches.append(f"{source.sheet}!{source.cell}={source.raw_value}")
        return "；".join(matches) if matches else "系统计算输入（详见输入快照）"

    def add(output: str, entity: str, field: str, value, inputs: str, rule: str):
        rows.append({
            "output": output,
            "entity_id": entity,
            "field": field,
            "value": value,
            "input_refs": inputs,
            "rule_version": RULE_VERSION,
            "rule": rule,
            "calculated_at": calculated_at,
            "input_hash": bundle.input_hash,
            "manual_override": "否",
        })

    for row in schedule:
        plan_inputs = source_cells(row.wbs, [("任务与工程量", "总工程量"), ("任务与工程量", "工期覆盖"), ("资源与定额", "当前数量"), ("资源与定额", "单位日产能"), ("外部约束", "最早可开始日期")])
        add("进度计划", row.wbs, "计划开始", row.early_start, plan_inputs, "CPM前推")
        add("进度计划", row.wbs, "计划完成", row.early_finish, plan_inputs, "CPM前推")
        add("关键线路", row.wbs, "总时差", row.total_float, "计划开始/完成及逻辑网络", "CPM后推")
    for row in forecast:
        actual_inputs = source_cells(row.wbs, [("实际进度", "实际完成工程量"), ("任务与工程量", "总工程量")])
        forecast_inputs = source_cells(row.wbs, [("实际进度", "剩余工期"), ("实际进度", "实际开始日期"), ("外部约束", "最早可开始日期")])
        add("进度偏差", row.wbs, "实际完成率", row.actual_percent, actual_inputs, "实物工程量法")
        add("滚动预测", row.wbs, "预测完成", row.forecast_finish, forecast_inputs, "保留逻辑剩余网络前推")
    for row in scenarios:
        scenario_inputs = source_cells(row.task_wbs, [("赶工参数", "最大资源投入"), ("赶工参数", "最短可行工期"), ("赶工参数", "资源效率系数"), ("资源与定额", "当前数量"), ("资源与定额", "硬上限"), ("资源与定额", "可增调量")])
        add("纠偏情景", row.task_wbs, "预计追回天数", row.recovered_days, scenario_inputs, "任务级资源效率系数")
    return rows
