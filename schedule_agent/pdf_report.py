from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


NAVY = colors.HexColor("#17324D")
TEAL = colors.HexColor("#2D7D7B")
RED = colors.HexColor("#C83E4D")
PALE = colors.HexColor("#EAF2F3")
LIGHT = colors.HexColor("#F3F6F7")
TEXT = colors.HexColor("#24343D")


def _font() -> str:
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    for path in candidates:
        if path.exists():
            name = "ScheduleChinese"
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
            return name
    return "Helvetica"


def export_pdf(result, path: Path) -> None:
    font = _font()
    page_size = landscape(A4)
    doc = BaseDocTemplate(
        str(path),
        pagesize=page_size,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=17 * mm,
        bottomMargin=14 * mm,
        title=f"{result.project.project_name}进度分析报告",
        author="市政道路工程进度计划 Agent",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates(PageTemplate(id="report", frames=[frame], onPage=lambda canvas, d: _page(canvas, d, result, font)))
    styles = getSampleStyleSheet()
    title = ParagraphStyle("cn-title", parent=styles["Title"], fontName=font, fontSize=21, leading=27, textColor=NAVY, alignment=TA_LEFT, spaceAfter=7 * mm)
    heading = ParagraphStyle("cn-heading", parent=styles["Heading2"], fontName=font, fontSize=13, leading=18, textColor=NAVY, spaceBefore=5 * mm, spaceAfter=3 * mm)
    body = ParagraphStyle("cn-body", parent=styles["BodyText"], fontName=font, fontSize=8.5, leading=12, textColor=TEXT)
    small = ParagraphStyle("cn-small", parent=body, fontSize=7, leading=9)
    tag = ParagraphStyle("cn-tag", parent=body, fontSize=9, leading=13, textColor=colors.white, backColor=TEAL, borderPadding=(3, 7, 3, 7))

    story = [
        Paragraph(result.project.project_name, title),
        Paragraph("市政道路工程进度计划与跟踪纠偏报告", heading),
        Paragraph(f"数据日期：{_d(result.project.data_date)}　运行编号：{result.run_id}　版本：V{result.version}　状态：{result.state}", body),
        Spacer(1, 4 * mm),
        Paragraph("正式输出资格：" + ("具备" if result.formal_eligible else "不具备（仅供草案复核）"), tag),
        Spacer(1, 5 * mm),
    ]
    summary = result.summary
    metrics = [
        ["合同竣工", "预测竣工", "对合同偏差", "关键任务", "重点预警", "阻断问题"],
        [_d(summary["contract_finish"]), _d(summary["forecast_finish"]), _days(summary["variance_contract_days"]), str(summary["critical_count"]), str(summary["alert_count"]), str(summary["blocking_count"])],
    ]
    story.append(_table(metrics, [38 * mm] * 6, font, header_rows=1, center=True))
    story.extend([Paragraph("管理结论", heading), Paragraph(_management_conclusion(result), body)])

    story.append(Paragraph("关键里程碑", heading))
    milestone_data = [["里程碑", "合同日期", "当前批准", "预测日期", "对当前偏差", "状态"]]
    milestone_data.extend([[m["name"], _d(m["contract_date"]), _d(m["current_date"]), _d(m["forecast_date"]), _days(m["variance_current_days"]), "延期风险" if m["alert"] else "正常"] for m in result.milestones])
    story.append(_table(milestone_data or [["暂无里程碑"]], [50 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm, 28 * mm], font))

    story.extend([PageBreak(), Paragraph("进度横道图", heading)])
    story.append(_gantt_table(result, font, small))

    story.extend([PageBreak(), Paragraph("关键线路与进度偏差", heading)])
    critical_data = [["WBS", "任务", "计划开始", "计划完成", "预测完成", "完成率偏差", "日期偏差", "预警原因"]]
    forecast = {row.wbs: row for row in result.forecast}
    for row in result.schedule:
        current = forecast.get(row.wbs)
        if row.critical or (current and current.alert):
            critical_data.append([row.wbs, row.name, _d(row.early_start), _d(row.early_finish), _d(current.forecast_finish if current else None), f"{current.variance_pp:+.1f}pp" if current else "-", _days(current.finish_variance_days if current else None), current.alert_reason if current else ""])
    story.append(_table(critical_data, [18 * mm, 43 * mm, 25 * mm, 25 * mm, 25 * mm, 24 * mm, 22 * mm, 67 * mm], font, small=True))

    story.append(Paragraph("纠偏候选方案", heading))
    scenario_data = [["情景", "措施与任务", "调整前/后", "资源增量", "预计追回", "风险与审批"]]
    for item in result.scenarios:
        scenario_data.append([item.scenario_id, f"{item.action_type}\n{item.task_wbs} {item.task_name}", f"{item.before_duration}/{item.after_duration or '-'} 工作日", str(item.resource_delta) if item.resource_delta is not None else "-", _days(item.recovered_days), f"{item.risk}；{item.approval}"])
    if len(scenario_data) == 1:
        scenario_data.append(["-", "当前没有达到组合预警阈值的关键任务", "-", "-", "-", "持续周度更新"])
    story.append(_table(scenario_data, [18 * mm, 56 * mm, 28 * mm, 25 * mm, 25 * mm, 96 * mm], font, small=True))

    story.extend([PageBreak(), Paragraph("问题清单与审计信息", heading)])
    issue_data = [["级别", "编码", "对象", "说明", "是否阻断"]]
    issue_data.extend([[item.severity, item.code, item.entity_id or "-", item.message, "是" if item.blocking else "否"] for item in result.issues])
    if len(issue_data) == 1:
        issue_data.append(["信息", "NO_ISSUES", "-", "未发现数据质量问题", "否"])
    story.append(_table(issue_data, [18 * mm, 38 * mm, 28 * mm, 132 * mm, 24 * mm], font, small=True))
    story.extend([
        Spacer(1, 5 * mm),
        Paragraph("审计摘要", heading),
        Paragraph(f"输入文件：{result.input_filename}<br/>输入 SHA-256：{result.input_hash}<br/>规则版本：{result.rule_version}<br/>计算时间：{result.calculated_at}<br/>追溯记录：{len(result.traceability)} 条", body),
        Spacer(1, 5 * mm),
        Paragraph("本报告由智能体生成，仅作为计划初稿与分析依据。基线、调整方案及正式签发须由计划工程师复核并由项目经理批准。", body),
    ])
    doc.build(story)


def _page(canvas, doc, result, font):
    canvas.saveState()
    canvas.setFont(font, 7)
    canvas.setFillColor(colors.HexColor("#637580"))
    canvas.drawString(doc.leftMargin, 8 * mm, f"{result.project.project_id}  |  规则 {result.rule_version}")
    canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 8 * mm, f"第 {doc.page} 页")
    canvas.setStrokeColor(colors.HexColor("#CCD8DC"))
    canvas.line(doc.leftMargin, 12 * mm, doc.pagesize[0] - doc.rightMargin, 12 * mm)
    canvas.restoreState()


def _table(data, widths, font, header_rows=1, center=False, small=False):
    style = [
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTSIZE", (0, 0), (-1, -1), 7 if small else 8),
        ("LEADING", (0, 0), (-1, -1), 9 if small else 11),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C6D2D7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
    ]
    if center:
        style.append(("ALIGN", (0, 0), (-1, -1), "CENTER"))
    return Table(data, colWidths=widths, repeatRows=header_rows, style=TableStyle(style), hAlign="LEFT")


def _gantt_table(result, font, small_style):
    if not result.schedule:
        return Paragraph("没有可排程任务。", small_style)
    start = min(row.early_start for row in result.schedule)
    finish = max(row.early_finish for row in result.schedule)
    total_days = max(1, (finish - start).days + 1)
    bucket = max(1, (total_days + 17) // 18)
    labels = []
    cursor = 0
    while cursor < total_days:
        labels.append(cursor)
        cursor += bucket
    data = [["任务"] + [(start.fromordinal(start.toordinal() + offset)).strftime("%m-%d") for offset in labels]]
    for row in result.schedule:
        cells = [f"{row.wbs} {row.name}"]
        for offset in labels:
            left = start.fromordinal(start.toordinal() + offset)
            right = start.fromordinal(min(finish.toordinal(), left.toordinal() + bucket - 1))
            cells.append("■" if row.early_start <= right and row.early_finish >= left else "")
        data.append(cells)
    widths = [55 * mm] + [10.5 * mm] * len(labels)
    table = _table(data, widths, font, small=True)
    commands = []
    for r_index, row in enumerate(result.schedule, start=1):
        fill = RED if row.critical else TEAL
        for c_index, value in enumerate(data[r_index][1:], start=1):
            if value:
                commands.extend([("BACKGROUND", (c_index, r_index), (c_index, r_index), fill), ("TEXTCOLOR", (c_index, r_index), (c_index, r_index), fill)])
    table.setStyle(TableStyle(commands))
    return table


def _management_conclusion(result) -> str:
    summary = result.summary
    parts = [f"本次分析共纳入 {summary['task_count']} 项任务，识别 {summary['critical_count']} 项关键任务和 {summary['alert_count']} 项重点预警。"]
    variance = summary.get("variance_contract_days")
    if variance is not None:
        if variance > 0:
            parts.append(f"预测竣工较合同竣工晚 {variance} 个工作日，应优先处理当前关键线路上的受阻任务。")
        else:
            parts.append("预测竣工未突破合同竣工日，仍需持续跟踪关键任务和外部约束。")
    if summary["blocking_count"]:
        parts.append(f"存在 {summary['blocking_count']} 项阻断问题，本报告只能作为草案。")
    return "".join(parts)


def _d(value) -> str:
    return value.isoformat() if isinstance(value, date) else (str(value) if value else "-")


def _days(value) -> str:
    if value is None:
        return "-"
    return f"{value:+d} 工作日" if isinstance(value, int) else str(value)
