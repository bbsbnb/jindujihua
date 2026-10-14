from __future__ import annotations

from datetime import date, timedelta

from .models import CalendarSpec


class WorkCalendar:
    def __init__(self, spec: CalendarSpec):
        self.spec = spec

    def is_workday(self, value: date) -> bool:
        if value in self.spec.extra_workdays:
            return True
        return value.weekday() in self.spec.work_weekdays and value not in self.spec.holidays

    def next_workday(self, value: date, inclusive: bool = True) -> date:
        cursor = value if inclusive else value + timedelta(days=1)
        while not self.is_workday(cursor):
            cursor += timedelta(days=1)
        return cursor

    def previous_workday(self, value: date, inclusive: bool = True) -> date:
        cursor = value if inclusive else value - timedelta(days=1)
        while not self.is_workday(cursor):
            cursor -= timedelta(days=1)
        return cursor

    def add_workdays(self, value: date, offset: int) -> date:
        cursor = self.next_workday(value) if offset >= 0 else self.previous_workday(value)
        if offset == 0:
            return cursor
        direction = 1 if offset > 0 else -1
        remaining = abs(offset)
        while remaining:
            cursor += timedelta(days=direction)
            if self.is_workday(cursor):
                remaining -= 1
        return cursor

    def workday_distance(self, start: date, end: date) -> int:
        """Signed workday-index difference; same workday is zero."""
        start_day = self.next_workday(start)
        end_day = self.next_workday(end) if end >= start else self.previous_workday(end)
        if start_day == end_day:
            return 0
        direction = 1 if end_day > start_day else -1
        cursor = start_day
        distance = 0
        while cursor != end_day:
            cursor += timedelta(days=direction)
            if self.is_workday(cursor):
                distance += direction
        return distance

    def working_days_inclusive(self, start: date, finish: date) -> int:
        if finish < start:
            return 0
        cursor = start
        count = 0
        while cursor <= finish:
            if self.is_workday(cursor):
                count += 1
            cursor += timedelta(days=1)
        return count

    def iter_workdays(self, start: date, finish: date):
        cursor = start
        while cursor <= finish:
            if self.is_workday(cursor):
                yield cursor
            cursor += timedelta(days=1)
