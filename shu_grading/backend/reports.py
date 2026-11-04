import io

from ..schemas import GradingReport, ReportRecord, GradingStatus, GradingResult, GradingSummary, StudentIdentity
from ..utils.excel import export_to_excel
from .errors import ServiceError


class ReportService:
    def __init__(self, store, tasks):
        self.store, self.tasks = store, tasks

    def export(self, uid, tid, confirmed_only=True):
        task = self.tasks.get(uid, tid)
        records = []
        for row in self.store.all('SELECT * FROM submissions WHERE task_id=? ORDER BY number', (tid,)):
            if confirmed_only and not row['confirmed']:
                continue
            encoded = row['final_result'] or row['ai_result']
            records.append(ReportRecord(student=StudentIdentity(name=row['label']),
                status=GradingStatus.SUCCESS if encoded else GradingStatus.AI_FAILED,
                grading=GradingResult.model_validate_json(encoded) if encoded else None,
                needs_review=not bool(row['confirmed']), error_message=row['error']))
        if not records:
            raise ServiceError(422, '还没有可导出的已确认成绩')
        scores = [r.grading.total_score for r in records if r.grading]
        summary = GradingSummary(total_students=len(records), graded_count=len(scores), failed_count=len(records)-len(scores),
            needs_review_count=sum(r.needs_review for r in records), average_score=sum(scores)/len(scores) if scores else None,
            max_score_actual=max(scores) if scores else None, min_score_actual=min(scores) if scores else None)
        batch = GradingReport(summary=summary, results=records, class_name=task['class_name'], assignment_description=task['assignment'])
        buffer = io.BytesIO()
        export_to_excel(batch, buffer, title=task['title'])
        buffer.seek(0)
        return buffer
