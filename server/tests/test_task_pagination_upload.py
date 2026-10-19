import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import server
from task_manager import TaskManager, TaskRecord


class TaskPaginationUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = TaskManager(self.root / 'tasks.json')
        self.addCleanup(self.manager.close)
        for name, value in [('TRANSLATES_DIR', self.root), ('TASK_MANAGER', self.manager)]:
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(self.manager, '_start_worker_locked')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = server.create_app().test_client()

    def tearDown(self):
        self.manager.close()
        self.manager.diagnostics.flush(timeout=2)

    def add(self, task_id, status='completed', **kwargs):
        record = TaskRecord(task_id, 'paper.pdf', 'openai', ['dual'], {}, self.root / task_id,
                            status=status, created_at='2026-01-01T00:00:00Z', **kwargs)
        self.manager._tasks[task_id] = record
        return record

    def test_keyset_pages_keep_all_active_and_tracked_results_without_full_serialization(self):
        for n in range(65):
            self.add(f'history-{n:03}')
        self.add('running', 'running')
        self.add('recoverable', 'incomplete', recovery_state='interrupted')
        calls = []
        original = TaskRecord.to_dict
        with patch.object(TaskRecord, 'to_dict', lambda record: (calls.append(record.task_id), original(record))[1]):
            first = self.client.post('/tasks/query', json={'tracked': ['history-000', 'deleted']}).json
        self.assertEqual(len(calls), 33)  # 30 history, 2 active, 1 pending local result
        self.assertEqual(first['missingTaskIds'], ['deleted'])
        self.assertEqual(first['pagination']['totalHistory'], 65)
        self.assertNotIn('history-000', first['visibleTaskIds'])
        seen = {v for v in first['visibleTaskIds'] if v.startswith('history')}
        cursor = first['pagination']['nextCursor']
        # Delete the boundary record and insert a newer one between page fetches.
        self.manager._tasks.pop('history-035')
        self.add('history-999')
        while cursor:
            page = self.client.post('/tasks/query', json={'cursor': cursor}).json
            self.assertIn('running', page['visibleTaskIds'])
            self.assertIn('recoverable', page['visibleTaskIds'])
            ids = {v for v in page['visibleTaskIds'] if v.startswith('history')}
            self.assertFalse(seen & ids)
            seen.update(ids)
            cursor = page['pagination']['nextCursor'] if page['pagination']['hasMore'] else None
        self.assertEqual(len(seen), 65)
        self.assertNotIn('history-999', seen)
        self.assertEqual(len(self.client.get('/tasks').json['tasks']), 67)

    def test_page_validation_expired_cursor_empty_history_and_active_sse(self):
        for invalid in ({'limit': 0}, {'limit': 101}, {'limit': True}, {'cursor': 'bad'},
                        {'tracked': 'one'}, {'tracked': [1]}, {'cursor': ['bad']}):
            self.assertEqual(self.client.post('/tasks/query', json=invalid).status_code, 400)
        self.add('live', 'queued')
        self.add('old')
        subscription = self.manager.subscribe(active_only=True)
        self.addCleanup(lambda: self.manager.unsubscribe(subscription))
        self.assertEqual([e['task']['taskId'] for e in subscription.initial_events], ['live'])
        cursor = self.manager.list_tasks_page(limit=1)['pagination']['nextCursor']
        self.manager._server_instance_id = 'restarted'
        result = self.client.post('/tasks/query', json={'cursor': cursor})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.json['code'], 'invalid_task_page')
        self.manager._tasks.pop('old')
        page = self.client.get('/tasks?view=dashboard&limit=30').json
        self.assertFalse(page['pagination']['hasMore'])
        self.assertEqual(page['pagination']['totalHistory'], 0)

    def upload(self, pdf=b'%PDF-binary\x00\xff', **metadata):
        response = self.client.post('/tasks', data={
            'metadata': json.dumps({'fileName': '论文.pdf', 'outputModes': ['dual'], 'service': 'openai',
                                    'requestId': 'binary-upload-0001', **metadata}),
            'file': (BytesIO(pdf), 'untrusted/path.pdf', 'application/pdf'),
        })
        # The test client does not register this callback without follow_redirects.
        response.call_on_close(response.request.input_stream.close)
        return self.enterContext(response)

    def test_upload_response_closes_large_multipart_stream(self):
        with self.upload(b'%PDF-1.7\n' * 100000) as response:
            self.assertEqual(response.status_code, 202)
            stream = response.request.input_stream
            self.assertFalse(stream.closed)
        self.assertTrue(stream.closed)

    def test_binary_upload_preserves_bytes_deduplicates_and_accepts_legacy_replay(self):
        pdf = b'%PDF-1.7\n\x00\xff' * 100000
        first = self.upload(pdf)
        self.assertEqual(first.status_code, 202, first.json)
        task_id = first.json['task']['taskId']
        record = self.manager._tasks[task_id]
        self.assertEqual(Path(record.request_payload['input_path']).read_bytes(), pdf)
        self.assertEqual(self.upload(pdf).json['task']['taskId'], task_id)
        legacy = self.client.post('/tasks', json={'fileName': '论文.pdf', 'outputModes': ['dual'],
            'service': 'openai', 'requestId': 'binary-upload-0001', 'fileContent': base64.b64encode(pdf).decode()})
        self.assertEqual(legacy.json['task']['taskId'], task_id)
        self.assertEqual(self.manager._pending.qsize(), 1)
        self.assertEqual(self.upload(pdf, targetLang='ja').status_code, 409)
        self.assertEqual({path.name for path in self.root.iterdir() if path.is_dir() and path.name != 'diagnostics'}, {task_id})

    def test_invalid_empty_and_oversize_uploads_leave_no_task_or_workspace(self):
        for metadata in ('null', '[]', '{bad', json.dumps({'fileContent': 'legacy'})):
            response = self.client.post('/tasks', data={'metadata': metadata, 'file': (BytesIO(b'pdf'), 'a.pdf')})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post('/tasks', data={'metadata': '{}'}).status_code, 400)
        self.assertEqual(self.upload(b'').status_code, 400)
        with patch.object(server, 'MAX_TASK_PDF_BYTES', 8):
            self.assertEqual(self.upload(b'123456789').status_code, 400)
        self.assertFalse(self.manager._tasks)
        self.assertFalse([path for path in self.root.iterdir() if path.is_dir()])


if __name__ == '__main__':
    unittest.main()
