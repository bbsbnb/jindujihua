from __future__ import annotations

import json
import shutil
import traceback
import uuid
from datetime import date, datetime
from pathlib import Path

from .analyzer import analyze_bundle
from .excel_io import WorkbookInputError, create_template, export_result, read_bundle
from .pdf_report import export_pdf
from .store import Store
from .wbs_rules import generate_candidates


class ScheduleService:
    def __init__(self, root: Path):
        self.root = root
        self.data_dir = root / "data"
        self.generated_dir = root / "generated"
        self.assets_dir = root / "assets"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data_dir / "schedule_agent.sqlite3")
        self.template_path = self.generated_dir / "市政道路进度计划标准模板.xlsx"
        self.sample_path = self.generated_dir / "市政道路进度计划示例.xlsx"
        if not self.template_path.exists():
            create_template(self.template_path, sample=False)
        if not self.sample_path.exists():
            create_template(self.sample_path, sample=True)

    def analyze(self, content: bytes, filename: str, actor: str = "计划工程师") -> dict:
        try:
            bundle = read_bundle(content, filename)
            self._normalize_bundle(bundle)
        except Exception as exc:
            self._record_import_error(filename, "读取与字段识别", exc)
            if isinstance(exc, ValueError):
                raise
            raise WorkbookInputError("读取进度表时发生异常，请检查合并表头、日期和编码字段后重试。") from exc
        candidate_notes = generate_candidates(bundle)
        run_id = "RUN-" + uuid.uuid4().hex[:10].upper()
        version = self.store.next_version(bundle.project.project_id)
        try:
            result = analyze_bundle(bundle, run_id, version)
        except Exception as exc:
            self._record_import_error(filename, "排程与偏差计算", exc)
            if isinstance(exc, TypeError):
                raise WorkbookInputError(
                    "进度表中存在无法比较的混合数据类型。请检查 WBS/任务编码、工期和日期列；编码列建议统一设为文本。"
                ) from exc
            raise
        self._apply_candidate_issues(result, candidate_notes)
        self._apply_import_issues(result, bundle)
        run_dir = self.generated_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        input_path = run_dir / "input.xlsx"
        input_path.write_bytes(content)
        self.store.add_run(run_id, bundle.project.project_id, version, result.formal_eligible, input_path, filename, bundle.input_hash, actor)
        try:
            self._write_outputs(result, run_dir)
        except Exception as exc:
            self._record_import_error(filename, "Excel/PDF 交付物生成", exc)
            raise WorkbookInputError("分析已完成，但生成 Excel/PDF 时发现不兼容数据，请检查异常长文本、日期和公式单元格。") from exc
        return self._payload(result)

    def analyze_sample(self) -> dict:
        return self.analyze(self.sample_path.read_bytes(), self.sample_path.name, "示例用户")

    def get_result(self, run_id: str) -> dict:
        metadata = self.store.get_run(run_id)
        if not metadata:
            raise ValueError("找不到该运行")
        content = Path(metadata["input_path"]).read_bytes()
        bundle = read_bundle(content, metadata["input_filename"])
        self._normalize_bundle(bundle)
        candidate_notes = generate_candidates(bundle)
        import_confirmed = bool(metadata.get("import_confirmed"))
        if import_confirmed:
            self._confirm_auto_content(bundle)
        result = analyze_bundle(bundle, run_id, metadata["version"])
        self._apply_candidate_issues(result, candidate_notes, import_confirmed)
        self._apply_import_issues(result, bundle, import_confirmed)
        result.formal_eligible = bool(metadata["formal_eligible"])
        result.state = metadata["state"]
        payload = self._payload(result, import_confirmed)
        payload["audit"] = self.store.audit(run_id)
        return payload

    def confirm_import(self, run_id: str, actor: str, acknowledged: bool) -> dict:
        if not acknowledged:
            raise ValueError("请先确认自动识别的字段映射、默认值和生成任务")
        metadata = self.store.get_run(run_id)
        if not metadata:
            raise ValueError("找不到该运行")
        preview = self.get_result(run_id)
        if not preview["requires_import_confirmation"]:
            raise ValueError("当前运行没有待确认的自动导入内容")
        comment = "已确认自动识别的字段映射、默认值、生成 WBS 及施工逻辑，进入人工复核流程。"
        self.store.confirm_import(run_id, actor, comment)
        result = self._recalculate(run_id, use_stored_eligibility=False)
        self.store.update_eligibility(run_id, result.formal_eligible)
        self._write_outputs(result, self.generated_dir / run_id)
        return self.get_result(run_id)

    def archive_plan(self, run_id: str, actor: str) -> dict:
        return self.store.archive_plan(run_id, actor)

    def transition(self, run_id: str, action: str, actor: str, comment: str) -> dict:
        self.store.transition(run_id, action, actor, comment)
        payload = self.get_result(run_id)
        result = self._recalculate(run_id)
        self._write_outputs(result, self.generated_dir / run_id)
        return payload

    def chat(self, run_id: str, message: str) -> str:
        result = self.get_result(run_id)
        text = message.strip()
        summary = result["summary"]
        if any(keyword in text for keyword in ["关键", "线路"]):
            path = " → ".join(summary.get("critical_path") or [])
            return f"当前基线关键任务为：{path or '无可识别关键线路'}。滚动预测中的关键性请结合“进度偏差”页查看。"
        if any(keyword in text for keyword in ["延期", "偏差", "风险"]):
            alerts = [row for row in result["forecast"] if row["alert"]]
            if not alerts:
                return "当前没有达到组合预警阈值的任务。建议继续按周更新实际量和剩余工期。"
            details = "；".join(f"{row['wbs']}：{row['alert_reason']}" for row in alerts[:5])
            return f"共 {len(alerts)} 项重点预警。{details}。"
        if any(keyword in text for keyword in ["纠偏", "建议", "追回"]):
            scenarios = result["scenarios"]
            if not scenarios:
                return "当前未生成纠偏情景，因为没有达到阈值的关键任务。"
            details = "；".join(f"{row['scenario_id']} 针对 {row['task_wbs']}，预计追回 {row['recovered_days']} 个工作日" if row["quantified"] else f"{row['scenario_id']} 针对 {row['task_wbs']}，需先补充赶工参数" for row in scenarios)
            return details + "。所有情景需计划工程师复核、项目经理批准后执行。"
        if any(keyword in text for keyword in ["问题", "缺失", "正式"]):
            blocking = [row for row in result["issues"] if row["blocking"]]
            if not blocking:
                return f"当前无阻断问题，运行状态为“{result['state']}”，具备进入复核流程的条件。"
            return f"当前有 {len(blocking)} 项阻断问题：" + "；".join(f"{row['entity_id'] or '项目'} {row['message']}" for row in blocking[:8]) + "。"
        finish = summary.get("forecast_finish") or "无法计算"
        variance = summary.get("variance_contract_days")
        return f"本次纳入 {summary['task_count']} 项任务，预测竣工 {finish}，对合同偏差 {variance if variance is not None else '-'} 个工作日，重点预警 {summary['alert_count']} 项。你可以继续问关键线路、延期风险、纠偏建议或阻断问题。"

    def artifact_path(self, run_id: str, kind: str) -> Path:
        names = {"excel": "进度计划分析.xlsx", "pdf": "进度计划分析报告.pdf", "input": "input.xlsx"}
        if kind not in names:
            raise ValueError("不支持的文件类型")
        path = self.generated_dir / run_id / names[kind]
        if not path.exists():
            raise ValueError("文件不存在")
        return path

    def _recalculate(self, run_id: str, use_stored_eligibility: bool = True):
        metadata = self.store.get_run(run_id)
        content = Path(metadata["input_path"]).read_bytes()
        bundle = read_bundle(content, metadata["input_filename"])
        self._normalize_bundle(bundle)
        candidate_notes = generate_candidates(bundle)
        import_confirmed = bool(metadata.get("import_confirmed"))
        if import_confirmed:
            self._confirm_auto_content(bundle)
        result = analyze_bundle(bundle, run_id, metadata["version"])
        self._apply_candidate_issues(result, candidate_notes, import_confirmed)
        self._apply_import_issues(result, bundle, import_confirmed)
        if use_stored_eligibility:
            result.formal_eligible = bool(metadata["formal_eligible"])
        result.state = metadata["state"]
        return result

    def _write_outputs(self, result, run_dir: Path) -> None:
        export_result(result, run_dir / "进度计划分析.xlsx")
        export_pdf(result, run_dir / "进度计划分析报告.pdf")
        (run_dir / "result.json").write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _payload(self, result, import_confirmed: bool = False) -> dict:
        payload = result.to_dict()
        confirmation_available = any(issue.code in {"AUTO_IMPORT_REVIEW", "AUTO_WBS_UNCONFIRMED"} for issue in result.issues)
        confirmable_codes = {"AUTO_IMPORT_REVIEW", "AUTO_WBS_UNCONFIRMED", "UNCONFIRMED_WBS", "UNCONFIRMED_RELATION"}
        payload["import_confirmed"] = import_confirmed
        payload["requires_import_confirmation"] = confirmation_available and not import_confirmed
        payload["unresolved_blocking_count"] = sum(
            issue.blocking and (not confirmation_available or issue.code not in confirmable_codes)
            for issue in result.issues
        )
        payload["downloads"] = {
            "excel": f"/api/runs/{result.run_id}/files/excel",
            "pdf": f"/api/runs/{result.run_id}/files/pdf",
            "input": f"/api/runs/{result.run_id}/files/input",
        }
        return payload

    @staticmethod
    def _candidate_issue(note):
        from .models import Issue
        return Issue("错误", "AUTO_WBS_UNCONFIRMED", f"自动拆解候选待确认：{note}", blocking=True)

    def _apply_candidate_issues(self, result, candidate_notes: list[str], confirmed: bool = False) -> None:
        for note in candidate_notes:
            if confirmed:
                from .models import Issue
                result.issues.insert(0, Issue("警告", "AUTO_WBS_CONFIRMED", f"已人工确认自动拆解：{note}", blocking=False))
            else:
                result.issues.insert(0, self._candidate_issue(note))
        if candidate_notes and not confirmed:
            result.formal_eligible = False
            result.summary["blocking_count"] += len(candidate_notes)

    @staticmethod
    def _apply_import_issues(result, bundle, confirmed: bool = False) -> None:
        if bundle.import_mode == "标准模板":
            return
        from .models import Issue

        result.issues.insert(0, Issue(
            "警告" if confirmed else "错误",
            "AUTO_IMPORT_CONFIRMED" if confirmed else "AUTO_IMPORT_REVIEW",
            "普通进度表的字段映射、默认值和推断关系已由计划工程师确认。" if confirmed else "普通进度表已自动识别并补充为分析草案；字段映射、默认值和推断关系必须由计划工程师确认。",
            bundle.project.project_id,
            "自动导入",
            not confirmed,
        ))
        for note in reversed(bundle.import_notes):
            result.issues.insert(1, Issue("警告", "AUTO_IMPORT_NOTE", note, field_name="自动导入", blocking=False))
        if not confirmed:
            result.formal_eligible = False
            result.summary["blocking_count"] += 1

    @staticmethod
    def _confirm_auto_content(bundle) -> None:
        for task in bundle.tasks:
            task.confirmed = True
        for relation in bundle.relations:
            relation.confirmed = True

    @staticmethod
    def _normalize_bundle(bundle) -> None:
        bundle.project.project_id = str(bundle.project.project_id or "").strip()
        bundle.project.project_name = str(bundle.project.project_name or "").strip()
        bundle.project.calendar_id = str(bundle.project.calendar_id or "").strip()
        bundle.calendar.calendar_id = str(bundle.calendar.calendar_id or "").strip()
        text_fields = {
            "tasks": ("wbs", "name", "discipline", "section", "station", "workfront", "unit", "status"),
            "relations": ("predecessor", "successor", "relation_type"),
            "resources": ("wbs", "resource_type", "source", "applicable_condition"),
            "crash_params": ("wbs", "overtime_condition", "approval_required"),
            "constraints": ("constraint_id", "wbs", "category", "owner", "status"),
            "actuals": ("wbs", "evidence_ref", "reporter"),
            "delay_causes": ("wbs", "category", "note", "responsibility", "controllable"),
            "period_plans": ("wbs",),
            "baselines": ("wbs",),
            "milestones": ("milestone_id", "name", "linked_wbs"),
        }
        for collection_name, fields in text_fields.items():
            for item in getattr(bundle, collection_name):
                for field in fields:
                    value = getattr(item, field)
                    setattr(item, field, "" if value is None else str(value).strip())
        for task in bundle.tasks:
            task.quantity = float(task.quantity or 0)
            task.duration_override = int(task.duration_override) if task.duration_override not in (None, "") else None
        for relation in bundle.relations:
            relation.relation_type = relation.relation_type.upper()
            relation.lag = int(relation.lag or 0)
        for actual in bundle.actuals:
            actual.actual_quantity = float(actual.actual_quantity or 0)
            actual.remaining_duration = int(actual.remaining_duration) if actual.remaining_duration not in (None, "") else None
        for source in bundle.source_refs:
            source.entity_type = str(source.entity_type or "").strip()
            source.entity_id = str(source.entity_id or "").strip()
            source.field_name = str(source.field_name or "").strip()

    def _record_import_error(self, filename: str, stage: str, exc: Exception) -> None:
        log_path = self.data_dir / "import_errors.log"
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        entry = (
            f"[{timestamp}] file={filename!r} stage={stage} error={type(exc).__name__}: {exc}\n"
            f"{traceback.format_exc()}\n"
        )
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(entry)
