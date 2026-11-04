import io
import json

import pytest
from openpyxl import load_workbook

from shu_grading.schemas import GradingResult
from shu_grading.utils.word_count import count_essay_words
from test_web_workflow import RESULT, detail, process, submit, task, web


@pytest.mark.parametrize('text,expected', [
    ('你好，世界！', 6), ('  甲\n乙\r\n丙\t丁　', 4),
    ('甲［疑字］，［疑字］。', 5), ('甲[疑字]乙', 3), ('', 0),
    ('ABC123，。', 8), ('𠮷［疑字］', 2),
])
def test_body_count(text, expected):
    assert count_essay_words(text) == expected


def test_title_and_model_estimate_are_excluded():
    result = GradingResult.model_validate({**RESULT, 'essay_title': '不计入的标题',
        'transcription': '正文［疑字］，完。', 'word_count': 1002})
    assert result.word_count == 6
    result = GradingResult.model_validate({**RESULT, 'transcription': '', 'word_count': 876})
    assert result.word_count is None


def test_historical_counts_and_export_use_body_and_ignore_forged_count(web):
    client, app, _ = web
    tid = task(client)
    sid = submit(client, tid)
    process(client, app)
    old = {**RESULT, 'transcription': '正文［疑字］，完。', 'essay_title': '标题', 'word_count': 1002}
    with app.state.store.connect() as db:
        db.execute('UPDATE submissions SET ai_result=? WHERE id=?', (json.dumps(old), sid))
    row = detail(client, tid)['submissions'][0]
    assert row['ai_result']['word_count'] == 6
    forged = {**old, 'word_count': 876, 'essay_title': '伪造标题', 'transcription': '伪造正文'}
    assert client.put(f'/api/submissions/{sid}/review', json={
        'result': forged, 'confirm': True, 'revision': row['revision']}).status_code == 200
    final = detail(client, tid)['submissions'][0]['final_result']
    assert final['word_count'] == 6 and final['essay_title'] == '标题'
    book = load_workbook(io.BytesIO(client.get(f'/api/tasks/{tid}/export').content))
    headers = [c.value for c in book['成绩总表'][1]]
    assert '状态' not in headers and '需复核' not in headers
    assert '正文分' in headers and '卷面扣分' in headers
    sheet = book['详细评语']
    count_column = next(c.column for c in sheet[1] if str(c.value).startswith('字数'))
    assert '含标点，不含标题' in sheet.cell(1, count_column).value
    assert sheet.cell(2, count_column).value == 6
