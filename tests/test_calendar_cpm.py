from __future__ import annotations

import unittest
from datetime import date

from schedule_agent.calendar import WorkCalendar
from schedule_agent.cpm import NetworkError, calculate_cpm
from schedule_agent.models import CalendarSpec, Relation, Task


def task(wbs: str) -> Task:
    return Task(wbs, wbs, "道路", "A区", "K0", wbs, "项", 1, "未开始", True)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.calendar = WorkCalendar(CalendarSpec("CAL", {0, 1, 2, 3, 4}, {date(2026, 10, 15)}, {date(2026, 10, 17)}))

    def test_add_workdays_honors_holiday_and_extra_day(self):
        self.assertEqual(self.calendar.add_workdays(date(2026, 10, 12), 3), date(2026, 10, 16))
        self.assertEqual(self.calendar.add_workdays(date(2026, 10, 12), 4), date(2026, 10, 17))
        self.assertEqual(self.calendar.workday_distance(date(2026, 10, 12), date(2026, 10, 17)), 4)


class CpmTests(unittest.TestCase):
    def setUp(self):
        self.calendar = WorkCalendar(CalendarSpec("CAL", {0, 1, 2, 3, 4}))
        self.start = date(2026, 10, 12)

    def rows(self, relation_type: str, lag: int = 0):
        result = calculate_cpm(
            [task("A"), task("B")],
            [Relation("A", "B", relation_type, lag)],
            {"A": 5, "B": 3},
            self.calendar,
            self.start,
        )
        return {row.wbs: row for row in result.rows}

    def test_fs_relationship_and_lag(self):
        rows = self.rows("FS", 2)
        self.assertEqual(rows["A"].early_finish, date(2026, 10, 16))
        self.assertEqual(rows["B"].early_start, date(2026, 10, 21))
        self.assertTrue(rows["A"].critical)
        self.assertTrue(rows["B"].critical)

    def test_ss_relationship(self):
        rows = self.rows("SS", 2)
        self.assertEqual(rows["B"].early_start, date(2026, 10, 14))

    def test_ff_relationship(self):
        rows = self.rows("FF", 0)
        self.assertEqual(rows["B"].early_finish, rows["A"].early_finish)

    def test_sf_relationship(self):
        rows = self.rows("SF", 2)
        self.assertEqual(rows["B"].early_finish, date(2026, 10, 14))

    def test_negative_lag(self):
        rows = self.rows("FS", -2)
        self.assertEqual(rows["B"].early_start, date(2026, 10, 15))

    def test_cycle_is_rejected(self):
        with self.assertRaisesRegex(NetworkError, "循环"):
            calculate_cpm(
                [task("A"), task("B")],
                [Relation("A", "B", "FS"), Relation("B", "A", "FS")],
                {"A": 2, "B": 2},
                self.calendar,
                self.start,
            )

    def test_mixed_numeric_and_text_ids_sort_safely(self):
        first = task("A")
        second = task("B")
        first.wbs = 1
        second.wbs = "2.1"
        result = calculate_cpm(
            [first, second],
            [],
            {1: 2, "2.1": 3},
            self.calendar,
            self.start,
        )
        self.assertEqual([row.wbs for row in result.rows], [1, "2.1"])


if __name__ == "__main__":
    unittest.main()
