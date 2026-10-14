from __future__ import annotations

from .models import InputBundle


RULES = [
    (("导改", "围挡"), "临时工程", "道路主线", "0"),
    (("雨水", "污水", "管沟", "管道"), "排水", "管沟", "1"),
    (("路床", "路基", "土方"), "道路", "道路主线", "2"),
    (("基层", "水稳"), "道路", "道路主线", "3"),
    (("沥青", "面层"), "道路", "道路主线", "4"),
    (("照明", "路灯"), "照明", "道路两侧", "5"),
    (("绿化", "苗木"), "绿化", "道路两侧", "6"),
    (("标线", "标志", "附属"), "交通工程", "道路主线", "7"),
]


def generate_candidates(bundle: InputBundle) -> list[str]:
    """Fill only missing planning hints; every generated task remains unconfirmed."""
    notes: list[str] = []
    counters: dict[str, int] = {}
    used = {task.wbs for task in bundle.tasks if task.wbs}
    for task in bundle.tasks:
        if task.wbs:
            continue
        discipline, workfront, prefix = _classify(task.name)
        counters[prefix] = counters.get(prefix, 0) + 1
        candidate = f"AUTO-{prefix}.{counters[prefix]}"
        while candidate in used:
            counters[prefix] += 1
            candidate = f"AUTO-{prefix}.{counters[prefix]}"
        task.wbs = candidate
        task.discipline = task.discipline or discipline
        task.workfront = task.workfront or workfront
        task.confirmed = False
        used.add(candidate)
        notes.append(f"{task.name} -> {candidate}（{task.discipline}/{task.workfront}）")
    return notes


def _classify(name: str) -> tuple[str, str, str]:
    for keywords, discipline, workfront, prefix in RULES:
        if any(keyword in name for keyword in keywords):
            return discipline, workfront, prefix
    return "待确认专业", "待确认工作面", "9"
