"""Stdlib-only producer used by the plugin's actual ZIP integration test."""
import json
from pathlib import Path
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server'))
from diagnostics import DiagnosticStore, safe_fields
from diagnostic_evidence import EvidenceLedger, analyze, link_failures, read_evidence
from observability import TaskMetricsCollector


def export_fixture():
    task = json.loads((ROOT / 'shared/diagnostics/fixtures/validation-failures.json').read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as directory:
        store = DiagnosticStore(Path(directory))
        store.record('task_state', task)
        assert store.flush(timeout=2)
        payload = store.export(task['taskId'])
        payload['tasks'] = [safe_fields(task)]
        payload['environment']['serviceVersion'] = '1.9.1'
        ledger = EvidenceLedger(directory, task['taskId'], task['attempt'])
        collector = TaskMetricsCollector(task_id=task['taskId'], provider='openai', model='fixture', evidence=ledger)
        native_task = payload['tasks'][0]
        refs = ['p-' + uuid.uuid4().hex for _ in native_task['failedParagraphs']]
        for failed, reference in zip(native_task['failedParagraphs'], refs):
            failed['paragraphRef'] = reference
        parent = collector.request_started(operation_id=uuid.uuid4().hex, paragraph_refs=refs)
        collector.request_finished(parent, succeeded=True, status_code=200, batch_size=4)
        collector.record_validation(parent, 'partial', invalid_outputs={
            index: row['validationCode'] for index, row in enumerate(native_task['failedParagraphs'])})
        for failed, reference in zip(native_task['failedParagraphs'], refs):
            previous = parent
            for retry in range(2):
                call = collector.request_started(operation_id=reference, paragraph_refs=[reference],
                    parent_call=previous, retry_cause='batch_fallback' if retry == 0 else 'output_validation')
                collector.request_finished(call, succeeded=True, status_code=200)
                collector.record_validation(call, 'rejected', code=failed['validationCode'])
                previous = call
            ledger.paragraphs([{**failed, 'paragraphState': 'failed', 'reason': 'private diagnostic fixture text'}])
        ledger.close('incomplete', native_task['translationSummary'])
        graph = link_failures(native_task, read_evidence(directory, task['taskId'], task['attempt'], task['status']))
        payload['evidence'] = {'taskEvidence': [graph], 'omittedTasks': 0}
        payload['analysis'] = analyze([native_task], payload['evidence'])
        return payload


if __name__ == '__main__':
    print(json.dumps(export_fixture(), ensure_ascii=False))
