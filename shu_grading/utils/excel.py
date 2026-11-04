"""Excel 导出.

把阅卷结果导出为老师友好的 Excel 表格:
- Sheet 1: 成绩总表(每个学生一行)
- Sheet 2: 详细评语(每个学生的详细评分)
- Sheet 3: 班级统计(平均分、分数段分布)

条件格式:
- needs_review=True 的行高亮黄色
- 失败的行高亮红色
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import BinaryIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from ..schemas import GradingReport, GradingStatus, GradingSummary

logger = logging.getLogger(__name__)


# 样式定义
HEADER_FONT = Font(name="Microsoft YaHei", size=11, bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="4472C4")
HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)

CELL_FONT = Font(name="Microsoft YaHei", size=10)
CELL_ALIGN = Alignment(horizontal="left", vertical="center", wrap_text=True)
NUMBER_ALIGN = Alignment(horizontal="center", vertical="center")

THIN_BORDER = Border(
    left=Side(style="thin", color="CCCCCC"),
    right=Side(style="thin", color="CCCCCC"),
    top=Side(style="thin", color="CCCCCC"),
    bottom=Side(style="thin", color="CCCCCC"),
)

REVIEW_FILL = PatternFill("solid", fgColor="FFEB9C")  # 浅黄
FAILED_FILL = PatternFill("solid", fgColor="FFC7CE")  # 浅红
SUCCESS_FILL = PatternFill("solid", fgColor="C6EFCE")  # 浅绿


def _apply_header_style(ws: Worksheet, row: int, cols: int) -> None:
    """应用表头样式."""
    for col in range(1, cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGN
        cell.border = THIN_BORDER


def _apply_data_style(ws: Worksheet, row: int, cols: int) -> None:
    """应用数据行样式."""
    for col in range(1, cols + 1):
        cell = ws.cell(row=row, column=col)
        # 教师文本及模型评语只作为文本，不作为 Excel 公式执行。
        if cell.data_type == "f":
            cell.data_type = "s"
        cell.font = CELL_FONT
        cell.border = THIN_BORDER
        if col == 1 or col > 5:  # 文字列
            cell.alignment = CELL_ALIGN
        else:  # 分数列居中
            cell.alignment = NUMBER_ALIGN


def _auto_size_columns(ws: Worksheet, min_width: int = 10, max_width: int = 60) -> None:
    """自动列宽."""
    for col_cells in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col_cells[0].column)
        for cell in col_cells:
            if cell.value is not None:
                cell_len = len(str(cell.value))
                if isinstance(cell.value, str) and "\n" in cell.value:
                    cell_len = max(len(line) for line in cell.value.split("\n"))
                if cell_len > max_len:
                    max_len = cell_len
        width = max(min_width, min(max_len + 4, max_width))
        ws.column_dimensions[col_letter].width = width


def write_scores_sheet(wb: Workbook, batch_result: GradingReport) -> Worksheet:
    """写入成绩总表.

    列: 姓名 | 班级 | 总分 | 维度1 | 维度2 | ... | 总评 | 正文分 | 卷面扣分
    """
    ws = wb.create_sheet("成绩总表")

    # 收集所有维度名
    all_criteria_names: list[str] = []
    for record in batch_result.results:
        if record.grading:
            for cs in record.grading.criterion_scores:
                if cs.name not in all_criteria_names:
                    all_criteria_names.append(cs.name)

    # 表头
    headers = ["姓名", "班级", "总分"] + all_criteria_names + ["总评", "正文分", "卷面扣分"]
    ws.append(headers)
    _apply_header_style(ws, 1, len(headers))
    ws.row_dimensions[1].height = 30

    # 数据行
    for record in batch_result.results:
        row_data: list = [
            record.student.name,
            batch_result.class_name or "(未填班级)",
        ]

        if record.grading:
            row_data.append(record.grading.total_score)
            for cname in all_criteria_names:
                cs = next(
                    (c for c in record.grading.criterion_scores if c.name == cname),
                    None,
                )
                row_data.append(cs.score if cs else "")
        else:
            row_data.append("")
            row_data.extend([""] * len(all_criteria_names))

        row_data.append(
            record.grading.overall_comment if record.grading else record.error_message or ""
        )
        row_data.append(round(sum(c.score for c in record.grading.criterion_scores), 1) if record.grading else "")
        row_data.append(record.grading.presentation_deduction if record.grading else "")

        ws.append(row_data)

        row_idx = ws.max_row
        _apply_data_style(ws, row_idx, len(headers))
        ws.row_dimensions[row_idx].height = 50

        # 条件格式:根据状态着色
        if record.status == GradingStatus.SUCCESS and not record.needs_review:
            fill = SUCCESS_FILL
        elif record.needs_review:
            fill = REVIEW_FILL
        else:
            fill = FAILED_FILL
        for col in range(1, len(headers) + 1):
            ws.cell(row=row_idx, column=col).fill = fill

    _auto_size_columns(ws)
    ws.freeze_panes = "A2"

    return ws


def write_details_sheet(wb: Workbook, batch_result: GradingReport) -> Worksheet:
    """写入详细评语 sheet."""
    ws = wb.create_sheet("详细评语")

    headers = [
        "姓名",
        "总分",
        "维度",
        "维度分",
        "维度满分",
        "等级",
        "维度评语",
        "亮点",
        "改进建议",
        "字数（含标点，不含标题；［疑字］计1字，空白不计）",
    ]
    ws.append(headers)
    _apply_header_style(ws, 1, len(headers))
    ws.row_dimensions[1].height = 30

    for record in batch_result.results:
        if not record.grading:
            continue

        first = True
        for cs in record.grading.criterion_scores:
            row = [
                record.student.name if first else "",
                record.grading.total_score if first else "",
                cs.name,
                cs.score,
                cs.max_score,
                cs.level.value if cs.level else "",
                cs.comment,
                "\n".join(record.grading.highlights) if first else "",
                "\n".join(record.grading.suggestions) if first else "",
                (record.grading.word_count if record.grading.word_count is not None else "") if first else "",
            ]
            ws.append(row)
            row_idx = ws.max_row
            _apply_data_style(ws, row_idx, len(headers))
            ws.row_dimensions[row_idx].height = 60
            first = False

        # 空行分隔
        ws.append([])

    _auto_size_columns(ws)
    ws.freeze_panes = "A2"

    return ws


def write_summary_sheet(wb: Workbook, summary: GradingSummary, title: str) -> Worksheet:
    """写入班级统计 sheet."""
    ws = wb.create_sheet("班级统计")

    ws.append([title])
    ws["A1"].font = Font(name="Microsoft YaHei", size=14, bold=True)
    ws.row_dimensions[1].height = 24

    # 基本统计
    ws.append([])
    ws.append(["统计项", "数值"])
    _apply_header_style(ws, 3, 2)

    rows = [
        ("本次导出答卷数", summary.total_students),
        ("成功批改", summary.graded_count),
        ("失败数", summary.failed_count),
        ("需复核数", summary.needs_review_count),
        ("平均分", f"{summary.average_score:.1f}" if summary.average_score is not None else "-"),
        ("最高分", f"{summary.max_score_actual:.1f}" if summary.max_score_actual is not None else "-"),
        ("最低分", f"{summary.min_score_actual:.1f}" if summary.min_score_actual is not None else "-"),
    ]
    for label, value in rows:
        ws.append([label, value])
        _apply_data_style(ws, ws.max_row, 2)

    # 分数段分布
    ws.append([])
    ws.append(["分数段分布"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)

    ws.append(["分数段", "人数"])
    _apply_header_style(ws, ws.max_row, 2)

    for seg, count in summary.distribution.items():
        ws.append([seg, count])
        _apply_data_style(ws, ws.max_row, 2)

    _auto_size_columns(ws)

    return ws


def export_to_excel(
    batch_result: GradingReport,
    output_path: Path | str | BinaryIO,
    title: str | None = None,
) -> Path:
    """导出阅卷结果为 Excel 文件.

    Args:
        batch_result: 批量阅卷结果
        output_path: 输出文件路径
        title: 标题(用于统计 sheet),默认用 assignment + class

    Returns:
        实际写入的文件路径
    """
    is_stream = hasattr(output_path, "write")
    if not is_stream:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    if title is None:
        title = (
            f"《{batch_result.assignment_description or '作文'}》阅卷结果"
            f" - {batch_result.class_name or ''}"
        ).strip(" -")

    wb = Workbook()
    # 删除默认 sheet
    wb.remove(wb.active)

    # Sheet 1: 成绩总表
    write_scores_sheet(wb, batch_result)

    # Sheet 2: 详细评语
    write_details_sheet(wb, batch_result)

    # Sheet 3: 班级统计
    write_summary_sheet(wb, batch_result.summary, title)

    wb.save(output_path if is_stream else str(output_path))
    logger.info("Excel 已导出")

    return output_path
