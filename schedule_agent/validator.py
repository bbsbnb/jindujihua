from __future__ import annotations

from collections import Counter, defaultdict

from .cpm import NetworkError, calculate_cpm
from .calendar import WorkCalendar
from .models import InputBundle, Issue


AUTHORITATIVE_SOURCES = {"企业定额", "项目实测"}
VALID_STATUSES = {"未开始", "进行中", "已完成"}
VALID_RELATIONS = {"FS", "SS", "FF", "SF"}


def validate_bundle(bundle: InputBundle, durations: dict[str, int]) -> list[Issue]:
    issues: list[Issue] = []
    task_ids = [task.wbs for task in bundle.tasks]
    duplicates = [wbs for wbs, count in Counter(task_ids).items() if count > 1]
    for wbs in duplicates:
        issues.append(Issue("错误", "DUPLICATE_WBS", "WBS 编码重复", wbs, "WBS编码", True))

    if bundle.project.contract_finish < bundle.project.start_date:
        issues.append(Issue("错误", "INVALID_CONTRACT_DATES", "合同竣工日早于开工日", blocking=True))
    if bundle.project.data_date < bundle.project.start_date:
        issues.append(Issue("错误", "INVALID_DATA_DATE", "数据日期早于项目开工日", blocking=True))
    if not bundle.calendar.work_weekdays:
        issues.append(Issue("错误", "EMPTY_CALENDAR", "项目日历没有工作日", blocking=True))

    resources_by_task = defaultdict(list)
    for resource in bundle.resources:
        resources_by_task[resource.wbs].append(resource)
        if resource.current_qty <= 0 or resource.unit_productivity <= 0:
            issues.append(Issue("错误", "INVALID_PRODUCTIVITY", "资源数量和单位产能必须大于零", resource.wbs, "资源与定额", True))
        if resource.current_qty > resource.hard_cap:
            issues.append(Issue("错误", "RESOURCE_OVER_CAP", f"{resource.resource_type} 当前投入超过硬上限", resource.wbs, "当前数量", True))
        if resource.source not in AUTHORITATIVE_SOURCES or not resource.confirmed:
            issues.append(Issue("错误", "UNCONFIRMED_RATE", "定额必须来自企业定额/项目实测并经确认", resource.wbs, "定额来源", True))

    actual_by_task = {actual.wbs: actual for actual in bundle.actuals}
    constraint_tasks = {item.wbs for item in bundle.constraints}
    for task in bundle.tasks:
        if not task.wbs or not task.name or not task.unit or task.quantity < 0:
            issues.append(Issue("错误", "TASK_REQUIRED_FIELD", "任务关键字段缺失或工程量非法", task.wbs, "任务与工程量", True))
        if task.status not in VALID_STATUSES:
            issues.append(Issue("错误", "INVALID_STATUS", f"无效任务状态: {task.status}", task.wbs, "任务状态", True))
        if not task.confirmed:
            issues.append(Issue("错误", "UNCONFIRMED_WBS", "自动生成或导入任务尚未确认", task.wbs, "是否确认", True))
        if not task.is_milestone and task.duration_override is None and not resources_by_task[task.wbs]:
            issues.append(Issue("错误", "MISSING_RESOURCE", "非里程碑任务缺少资源与产能", task.wbs, "资源与定额", True))
        if not task.section or not task.workfront:
            issues.append(Issue("错误", "MISSING_LOCATION", "任务缺少区段或工作面", task.wbs, "区段/工作面", True))
        actual = actual_by_task.get(task.wbs)
        if actual and (actual.actual_quantity < 0 or actual.actual_quantity > task.quantity):
            issues.append(Issue("错误", "INVALID_ACTUAL_QUANTITY", "实际完成工程量必须在零与总工程量之间", task.wbs, "实际完成工程量", True))
        if task.status == "未开始" and actual and actual.actual_quantity > 0:
            issues.append(Issue("错误", "STATUS_PROGRESS_MISMATCH", "未开始任务不能填报已完工程量", task.wbs, "任务状态", True))
        if task.status == "已完成" and not task.is_milestone and actual and actual.actual_quantity != task.quantity:
            issues.append(Issue("错误", "INCOMPLETE_COMPLETED_TASK", "已完成任务的实际工程量必须等于总工程量", task.wbs, "实际完成工程量", True))
        if task.status in {"进行中", "已完成"}:
            if actual is None or not actual.evidence_ref:
                issues.append(Issue("错误", "MISSING_EVIDENCE", "已报实际进度缺少来源编号", task.wbs, "证据来源编号", True))
        if task.status == "进行中" and (actual is None or actual.remaining_duration is None or actual.remaining_duration <= 0):
            issues.append(Issue("错误", "MISSING_REMAINING_DURATION", "进行中任务必须填报正数剩余工期", task.wbs, "剩余工期", True))

    for relation in bundle.relations:
        if relation.relation_type.upper() not in VALID_RELATIONS:
            issues.append(Issue("错误", "INVALID_RELATION", f"关系类型必须为 FS/SS/FF/SF: {relation.relation_type}", relation.successor, "关系类型", True))
        if not relation.confirmed:
            issues.append(Issue("错误", "UNCONFIRMED_RELATION", "逻辑关系尚未确认", relation.successor, "是否确认", True))

    caps_by_type = defaultdict(set)
    for resource in bundle.resources:
        caps_by_type[resource.resource_type].add(resource.hard_cap)
    for resource_type, caps in caps_by_type.items():
        if len(caps) > 1:
            issues.append(Issue("错误", "INCONSISTENT_RESOURCE_CAP", f"同类资源 {resource_type} 的项目硬上限不一致", resource_type, "硬上限", True))

    for constraint in bundle.constraints:
        if not constraint.confirmed:
            issues.append(Issue("错误", "UNCONFIRMED_CONSTRAINT", "外部约束尚未确认", constraint.wbs, "是否确认", True))
        if not constraint.owner or not constraint.status:
            issues.append(Issue("错误", "CONSTRAINT_REQUIRED_FIELD", "外部约束缺少责任方或状态", constraint.wbs, "外部约束", True))

    known = set(task_ids)
    for collection, label in [
        (bundle.resources, "资源"),
        (bundle.actuals, "实际进度"),
        (bundle.crash_params, "赶工参数"),
        (bundle.constraints, "外部约束"),
    ]:
        for item in collection:
            if item.wbs not in known:
                issues.append(Issue("错误", "UNKNOWN_WBS", f"{label}引用不存在的 WBS", item.wbs, label, True))

    try:
        calculate_cpm(
            bundle.tasks,
            bundle.relations,
            durations,
            WorkCalendar(bundle.calendar),
            bundle.project.start_date,
            {item.wbs: item.not_before for item in bundle.constraints if item.wbs in known},
        )
    except NetworkError as exc:
        issues.append(Issue("错误", "NETWORK_ERROR", str(exc), blocking=True))

    if not bundle.milestones:
        issues.append(Issue("警告", "MISSING_MILESTONES", "未配置合同关键里程碑，仅检查项目竣工日"))
    if not constraint_tasks:
        issues.append(Issue("警告", "MISSING_CONSTRAINTS", "未录入外部约束，请确认交通导改、征拆迁改、审批和材料到货是否适用"))
    return issues
