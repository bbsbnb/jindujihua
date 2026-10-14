from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date

from .calendar import WorkCalendar
from .models import Relation, ScheduleRow, Task


class NetworkError(ValueError):
    pass


@dataclass(slots=True)
class NetworkResult:
    rows: list[ScheduleRow]
    project_finish: date
    order: list[str]


def relation_weight(kind: str, predecessor_duration: int, successor_duration: int, lag: int) -> int:
    """Return minimum successor start offset from predecessor start."""
    kind = kind.upper()
    if kind == "FS":
        return predecessor_duration + lag
    if kind == "SS":
        return lag
    if kind == "FF":
        return predecessor_duration - successor_duration + lag
    if kind == "SF":
        return lag - successor_duration + 1
    raise NetworkError(f"不支持的逻辑关系: {kind}")


def calculate_cpm(
    tasks: list[Task],
    relations: list[Relation],
    durations: dict[str, int],
    calendar: WorkCalendar,
    project_start: date,
    start_bounds: dict[str, date] | None = None,
) -> NetworkResult:
    if not tasks:
        raise NetworkError("没有可排程任务")

    start_bounds = start_bounds or {}
    task_map = {task.wbs: task for task in tasks}
    if len(task_map) != len(tasks):
        raise NetworkError("WBS 编码必须唯一")

    incoming: dict[str, list[Relation]] = defaultdict(list)
    outgoing: dict[str, list[Relation]] = defaultdict(list)
    indegree = {task.wbs: 0 for task in tasks}
    for relation in relations:
        if relation.predecessor not in task_map or relation.successor not in task_map:
            raise NetworkError(f"逻辑关系引用不存在任务: {relation.predecessor} -> {relation.successor}")
        if relation.predecessor == relation.successor:
            raise NetworkError(f"任务不能依赖自身: {relation.predecessor}")
        incoming[relation.successor].append(relation)
        outgoing[relation.predecessor].append(relation)
        indegree[relation.successor] += 1

    queue = deque(sorted((wbs for wbs, degree in indegree.items() if degree == 0), key=str))
    order: list[str] = []
    while queue:
        wbs = queue.popleft()
        order.append(wbs)
        for relation in outgoing[wbs]:
            indegree[relation.successor] -= 1
            if indegree[relation.successor] == 0:
                queue.append(relation.successor)

    if len(order) != len(tasks):
        cyclic = sorted((wbs for wbs, degree in indegree.items() if degree > 0), key=str)
        raise NetworkError(f"逻辑关系存在循环: {', '.join(str(item) for item in cyclic)}")

    origin = calendar.next_workday(project_start)
    early: dict[str, int] = {}
    for wbs in order:
        duration = max(1, int(durations[wbs]))
        bound_date = start_bounds.get(wbs, origin)
        earliest = max(0, calendar.workday_distance(origin, calendar.next_workday(bound_date)))
        for relation in incoming[wbs]:
            pred = relation.predecessor
            weight = relation_weight(
                relation.relation_type,
                max(1, int(durations[pred])),
                duration,
                relation.lag,
            )
            earliest = max(earliest, early[pred] + weight)
        early[wbs] = earliest

    project_finish_index = max(early[wbs] + max(1, int(durations[wbs])) - 1 for wbs in order)
    late: dict[str, int] = {}
    for wbs in reversed(order):
        duration = max(1, int(durations[wbs]))
        candidates: list[int] = []
        for relation in outgoing[wbs]:
            successor = relation.successor
            weight = relation_weight(
                relation.relation_type,
                duration,
                max(1, int(durations[successor])),
                relation.lag,
            )
            candidates.append(late[successor] - weight)
        late[wbs] = min(candidates) if candidates else project_finish_index - duration + 1

    rows: list[ScheduleRow] = []
    for wbs in order:
        task = task_map[wbs]
        duration = max(1, int(durations[wbs]))
        early_start_index = early[wbs]
        early_finish_index = early_start_index + duration - 1
        total_float = late[wbs] - early_start_index
        successor_slack: list[int] = []
        for relation in outgoing[wbs]:
            successor = relation.successor
            weight = relation_weight(
                relation.relation_type,
                duration,
                max(1, int(durations[successor])),
                relation.lag,
            )
            successor_slack.append(early[successor] - weight - early_start_index)
        free_float = min(successor_slack) if successor_slack else project_finish_index - early_finish_index
        rows.append(
            ScheduleRow(
                wbs=wbs,
                name=task.name,
                duration=duration,
                early_start=calendar.add_workdays(origin, early_start_index),
                early_finish=calendar.add_workdays(origin, early_finish_index),
                late_start=calendar.add_workdays(origin, late[wbs]),
                late_finish=calendar.add_workdays(origin, late[wbs] + duration - 1),
                total_float=total_float,
                free_float=free_float,
                critical=total_float <= 0,
                section=task.section,
                workfront=task.workfront,
            )
        )

    return NetworkResult(
        rows=rows,
        project_finish=calendar.add_workdays(origin, project_finish_index),
        order=order,
    )
