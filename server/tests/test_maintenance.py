import tempfile
import threading
import unittest
import sys
from unittest.mock import patch
from pathlib import Path

from flask import Flask, Response
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maintenance import MaintenanceGate, control_token, install_routes
from text_translation import TextTranslationService, TextTranslationError
from translation_memory import TranslationMemory


class MaintenanceTests(unittest.TestCase):
    def test_timeout_does_not_hide_running_provider_worker(self):
        finished = threading.Event()
        service = TextTranslationService(timeout=0.01)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                memory = TranslationMemory(Path(temporary) / 'memory.sqlite3')
                gate = MaintenanceGate(service.busy)
                def generate(*args, **kwargs):
                    finished.wait(5)
                    return {'translation': '结果', 'provider': 'test'}
                with patch.object(service, '_generate', side_effect=generate):
                    with self.assertRaises(TextTranslationError):
                        service.translate({'text': 'timeout remains busy'}, memory)
                    self.assertTrue(gate.snapshot()['busy'])
                    self.assertFalse(gate.acquire(0))
                    finished.set()
                    service.executor.shutdown(wait=True)
                    self.assertTrue(gate.acquire(0))
        finally:
            finished.set()
            service.executor.shutdown(wait=True)

    def test_idle_busy_and_expiring_lease(self):
        now = [0.0]
        busy = [False]
        gate = MaintenanceGate(lambda: busy[0], lambda: now[0])
        now[0] = 60
        self.assertTrue(gate.enter())
        self.assertFalse(gate.acquire(0))
        gate.leave()
        now[0] += 60
        busy[0] = True
        self.assertFalse(gate.acquire())
        busy[0] = False
        gate.snapshot()
        now[0] += 60
        self.assertTrue(gate.acquire())
        self.assertFalse(gate.enter())
        now[0] += 121
        self.assertTrue(gate.enter())
        gate.leave()

    def test_admission_and_maintenance_are_atomic(self):
        for _ in range(50):
            gate = MaintenanceGate(lambda: False)
            barrier = threading.Barrier(3)
            outcomes = []
            def attempt(action):
                barrier.wait()
                outcomes.append(action())
            threads = [threading.Thread(target=attempt, args=(gate.enter,)),
                       threading.Thread(target=attempt, args=(lambda: gate.acquire(0),))]
            for thread in threads: thread.start()
            barrier.wait()
            for thread in threads: thread.join()
            self.assertEqual(sum(outcomes), 1)

    def test_authenticated_loopback_and_stream_lifetime(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            app = Flask(__name__)
            gate = install_routes(app, lambda: False, lambda: directory)
            @app.post('/work')
            def work(): return {'accepted': True}
            @app.post('/stream')
            def stream(): return Response(iter(['one', 'two']))
            client = app.test_client()
            headers = {'X-PDF2ZH-Control-Token': control_token(directory)}
            self.assertEqual(client.post('/internal/maintenance', json={'idleSeconds': 0}).status_code, 403)
            self.assertEqual(client.post('/internal/maintenance', headers={**headers,'Origin':'http://bad'}, json={'idleSeconds':0}).status_code,403)
            self.assertEqual(client.post('/internal/maintenance', headers=headers, json={'idleSeconds':0}, environ_overrides={'REMOTE_ADDR':'192.168.1.1'}).status_code,403)
            response = client.post('/stream', buffered=False)
            self.assertEqual(gate.snapshot()['activeRequests'], 1)
            self.assertEqual(client.post('/internal/maintenance', headers=headers, json={'idleSeconds':0}).status_code,409)
            response.close()
            self.assertEqual(gate.snapshot()['activeRequests'], 0)
            self.assertEqual(client.post('/internal/maintenance', headers=headers, json={'idleSeconds':0}).status_code,200)
            rejected = client.post('/work')
            self.assertEqual(rejected.status_code,503)
            self.assertIs(rejected.json['accepted'],False)
            self.assertEqual(rejected.headers['X-PDF2ZH-Maintenance'],'not-accepted')
            client.delete('/internal/maintenance', headers=headers)
            self.assertEqual(client.post('/work').status_code,200)

    def test_dashboard_post_polling_is_read_only_during_idle_and_maintenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            now = [0.0]
            gate = MaintenanceGate(lambda: False, lambda: now[0])
            app = Flask(__name__)
            with patch('maintenance.MaintenanceGate', return_value=gate):
                install_routes(app, lambda: False, lambda: Path(temporary))
            @app.post('/tasks/query')
            def query(): return {'tasks': []}
            @app.post('/tasks')
            def create(): return {'accepted': True}
            @app.get('/tasks/one/result')
            def result(): return Response(iter(['pdf']))
            @app.post('/tasks/one/cancel')
            def cancel(): return {'status': 'cancelled'}
            client = app.test_client()
            for second in range(5, 105, 5):
                now[0] = second
                self.assertEqual(client.post('/tasks/query', json={'limit': 30}).status_code, 200)
            self.assertEqual(gate.snapshot()['idleSeconds'], 100)
            self.assertTrue(gate.acquire(60))
            self.assertEqual(client.post('/tasks/query', json={}).status_code, 200)
            for path in ('/tasks', '/tasks/one/cancel', '/tasks/query/extra'):
                response = client.post(path)
                self.assertEqual(response.status_code, 503)
                self.assertIs(response.json['accepted'], False)
            self.assertEqual(client.get('/tasks/one/result').status_code, 503)
            gate.release()
            now[0] += 60
            response = client.get('/tasks/one/result', buffered=False)
            self.assertFalse(gate.acquire(0))
            response.close()
            self.assertEqual(gate.snapshot()['activeRequests'], 0)
