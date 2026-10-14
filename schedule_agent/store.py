from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    formal_eligible INTEGER NOT NULL,
                    input_path TEXT NOT NULL,
                    input_filename TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    comment TEXT NOT NULL DEFAULT '',
                    import_confirmed INTEGER NOT NULL DEFAULT 0,
                    archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT,
                    actor TEXT NOT NULL,
                    comment TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS manual_adjustments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    wbs TEXT NOT NULL,
                    actual_percent REAL NOT NULL,
                    remaining_duration INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    note TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(runs)").fetchall()}
            if "import_confirmed" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN import_confirmed INTEGER NOT NULL DEFAULT 0")
            if "archived" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")

    def next_version(self, project_id: str) -> int:
        with self._connect() as db:
            row = db.execute("SELECT COALESCE(MAX(version), 0) AS value FROM runs WHERE project_id = ?", (project_id,)).fetchone()
        return int(row["value"]) + 1

    def add_run(self, run_id: str, project_id: str, version: int, formal_eligible: bool, input_path: Path, input_filename: str, input_hash: str, actor: str) -> None:
        now = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO runs (
                    run_id, project_id, version, state, formal_eligible, input_path,
                    input_filename, input_hash, created_at, updated_at, actor, comment,
                    import_confirmed, archived
                ) VALUES (?, ?, ?, '草案', ?, ?, ?, ?, ?, ?, ?, '', 0, 0)""",
                (run_id, project_id, version, int(formal_eligible), str(input_path), input_filename, input_hash, now, now, actor),
            )
            db.execute(
                "INSERT INTO audit_events(run_id, action, from_state, to_state, actor, comment, created_at) VALUES (?, '创建运行', NULL, '草案', ?, '', ?)",
                (run_id, actor, now),
            )

    def create_adjustment_run(self, source_run_id: str, run_id: str, version: int, input_path: Path, actor: str) -> dict:
        source = self.get_run(source_run_id)
        if not source:
            raise ValueError("找不到原运行")
        now = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO runs (
                    run_id, project_id, version, state, formal_eligible, input_path,
                    input_filename, input_hash, created_at, updated_at, actor, comment,
                    import_confirmed, archived
                ) VALUES (?, ?, ?, '草案', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    run_id, source["project_id"], version, source["formal_eligible"], str(input_path),
                    source["input_filename"], source["input_hash"], now, now, actor,
                    f"基于 {source_run_id} 创建人工调整版本", source["import_confirmed"],
                ),
            )
            latest_adjustments = db.execute(
                """SELECT adjustment.*
                FROM manual_adjustments adjustment
                JOIN (
                    SELECT wbs, MAX(id) AS latest_id
                    FROM manual_adjustments
                    WHERE run_id = ?
                    GROUP BY wbs
                ) latest ON latest.latest_id = adjustment.id""",
                (source_run_id,),
            ).fetchall()
            db.executemany(
                """INSERT INTO manual_adjustments(
                    run_id, wbs, actual_percent, remaining_duration, reason,
                    action_type, note, actor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(
                    run_id, row["wbs"], row["actual_percent"], row["remaining_duration"],
                    row["reason"], row["action_type"], row["note"], row["actor"], now,
                ) for row in latest_adjustments],
            )
            db.execute(
                "INSERT INTO audit_events(run_id, action, from_state, to_state, actor, comment, created_at) VALUES (?, '创建调整版本', NULL, '草案', ?, ?, ?)",
                (run_id, actor, f"基于已冻结版本 {source_run_id} 创建，不修改原批准基线", now),
            )
        return self.get_run(run_id)

    def list_runs(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM runs WHERE archived = 0 ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def confirm_import(self, run_id: str, actor: str, comment: str) -> dict:
        run = self.get_run(run_id)
        if not run:
            raise ValueError("找不到该运行")
        if run["state"] != "草案":
            raise ValueError("只有草案可以确认自动导入内容")
        if not actor.strip():
            raise ValueError("操作人不能为空")
        now = _now()
        with self._connect() as db:
            db.execute(
                "UPDATE runs SET import_confirmed = 1, updated_at = ?, actor = ?, comment = ? WHERE run_id = ?",
                (now, actor, comment, run_id),
            )
            db.execute(
                "INSERT INTO audit_events(run_id, action, from_state, to_state, actor, comment, created_at) VALUES (?, '确认自动导入', '草案', '草案', ?, ?, ?)",
                (run_id, actor, comment, now),
            )
        return self.get_run(run_id)

    def update_eligibility(self, run_id: str, formal_eligible: bool) -> None:
        with self._connect() as db:
            db.execute("UPDATE runs SET formal_eligible = ?, updated_at = ? WHERE run_id = ?", (int(formal_eligible), _now(), run_id))

    def archive_plan(self, run_id: str, actor: str) -> dict:
        run = self.get_run(run_id)
        if not run or run["archived"]:
            raise ValueError("找不到该计划或计划已删除")
        if not actor.strip():
            raise ValueError("操作人不能为空")
        now = _now()
        with self._connect() as db:
            rows = db.execute(
                "SELECT run_id, state FROM runs WHERE input_filename = ? AND archived = 0",
                (run["input_filename"],),
            ).fetchall()
            run_ids = [row["run_id"] for row in rows]
            db.execute(
                "UPDATE runs SET archived = 1, updated_at = ?, actor = ?, comment = '用户从导航删除计划' WHERE input_filename = ? AND archived = 0",
                (now, actor, run["input_filename"]),
            )
            db.executemany(
                "INSERT INTO audit_events(run_id, action, from_state, to_state, actor, comment, created_at) VALUES (?, '删除计划', ?, '已归档', ?, '从导航移除并保留历史文件与审计记录', ?)",
                [(row["run_id"], row["state"], actor, now) for row in rows],
            )
        return {"input_filename": run["input_filename"], "archived_runs": len(run_ids)}

    def save_manual_adjustment(
        self,
        run_id: str,
        wbs: str,
        actual_percent: float,
        remaining_duration: int,
        reason: str,
        action_type: str,
        note: str,
        actor: str,
    ) -> None:
        run = self.get_run(run_id)
        if not run or run["archived"]:
            raise ValueError("找不到该运行")
        now = _now()
        comment = (
            f"{wbs}：实际完成率 {actual_percent:g}%，剩余工期 {remaining_duration} 个工作日；"
            f"原因：{reason}；措施：{action_type}；说明：{note}"
        )
        with self._connect() as db:
            db.execute(
                """INSERT INTO manual_adjustments(
                    run_id, wbs, actual_percent, remaining_duration, reason,
                    action_type, note, actor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, wbs, actual_percent, remaining_duration, reason, action_type, note, actor, now),
            )
            db.execute(
                "INSERT INTO audit_events(run_id, action, from_state, to_state, actor, comment, created_at) VALUES (?, '人工调整进度', ?, ?, ?, ?, ?)",
                (run_id, run["state"], run["state"], actor, comment, now),
            )

    def get_manual_adjustments(self, run_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT adjustment.*
                FROM manual_adjustments adjustment
                JOIN (
                    SELECT wbs, MAX(id) AS latest_id
                    FROM manual_adjustments
                    WHERE run_id = ?
                    GROUP BY wbs
                ) latest ON latest.latest_id = adjustment.id
                ORDER BY adjustment.id""",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def transition(self, run_id: str, action: str, actor: str, comment: str) -> dict:
        run = self.get_run(run_id)
        if not run:
            raise ValueError("找不到该运行")
        transitions = {
            ("草案", "review"): "已复核",
            ("已复核", "approve"): "已批准",
            ("已复核", "reject"): "已驳回",
            ("已批准", "reject"): "已驳回",
            ("已驳回", "resubmit"): "草案",
        }
        target = transitions.get((run["state"], action))
        if not target:
            raise ValueError(f"状态 {run['state']} 不允许执行 {action}")
        if action in {"review", "approve"} and not run["formal_eligible"]:
            raise ValueError("存在阻断问题，不能复核或批准")
        if not actor.strip():
            raise ValueError("操作人不能为空")
        if action in {"reject", "resubmit"} and not comment.strip():
            raise ValueError("驳回或重提必须填写原因")
        now = _now()
        with self._connect() as db:
            db.execute("UPDATE runs SET state = ?, updated_at = ?, actor = ?, comment = ? WHERE run_id = ?", (target, now, actor, comment, run_id))
            db.execute(
                "INSERT INTO audit_events(run_id, action, from_state, to_state, actor, comment, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, action, run["state"], target, actor, comment, now),
            )
        return self.get_run(run_id)

    def audit(self, run_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM audit_events WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [dict(row) for row in rows]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
