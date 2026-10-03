"""Codex protocol tests using fake transports and local Python stdio peers only."""
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import codex_client
from codex_client import CodexClient, CodexError, _Transport, resolve_codex_path


def event(method, thread_id, turn_id, **params):
    return {"method": method, "params": {"threadId": thread_id, "turnId": turn_id, **params}}


def answer(thread_id, turn_id, text="译文", phase="final_answer", item_id="answer"):
    return event("item/completed", thread_id, turn_id,
                 item={"id": item_id, "type": "agentMessage", "phase": phase, "text": text})


def completed(thread_id, turn_id, status="completed", **fields):
    return event("turn/completed", thread_id, turn_id,
                 turn={"id": turn_id, "status": status, **fields})


class ScriptedTransport:
    """A deterministic peer; scripts emit notifications before the RPC reply."""
    def __init__(self, script=None):
        self.script = script or (lambda thread, turn, _: [answer(thread, turn), completed(thread, turn)])
        self.calls = []
        self.queues = {}
        self.retired = []
        self.closed = False
        self._failure = None
        self.account = {"type": "chatgpt"}
        self.models = [{"id": "catalog-entry", "model": "gpt-6-luna", "displayName": "Luna",
                        "defaultReasoningEffort": "medium",
                        "supportedReasoningEfforts": [{"reasoningEffort": "none"},
                                                       {"reasoningEffort": "medium"}]}]
        self.thread_override = {}
        self.interrupt_acknowledged = True
        self.thread_counter = 0
        self.turn_started = threading.Event()
        self.lock = threading.Lock()

    def call(self, method, params, **kwargs):
        with self.lock:
            self.calls.append((method, params))
        if method == "account/read":
            return {"account": self.account}
        if method == "model/list":
            return {"data": self.models, "nextCursor": None}
        if method == "thread/start":
            with self.lock:
                self.thread_counter += 1
                thread_id = "thread-" + str(self.thread_counter)
            return {"thread": {"id": thread_id, "ephemeral": True}, "instructionSources": [],
                    "model": params["model"], "modelProvider": "openai", "approvalPolicy": "never",
                    "activePermissionProfile": {"id": params["permissions"]},
                    "sandbox": {"type": "readOnly", "networkAccess": False},
                    "serviceTier": "default", "reasoningEffort": params["config"]["model_reasoning_effort"],
                    **self.thread_override}
        if method == "turn/start":
            thread_id = params["threadId"]
            turn_id = "turn-" + thread_id
            self.turn_started.set()
            for message in self.script(thread_id, turn_id, params):
                self.events(thread_id).put(message)
            return {"turn": {"id": turn_id}}
        if method == "turn/interrupt":
            if not self.interrupt_acknowledged:
                raise CodexError("codex_timeout", "synthetic interrupt timeout", 504)
            self.events(params["threadId"]).put(completed(params["threadId"], params["turnId"], "interrupted"))
            return {}
        if method == "thread/unsubscribe":
            return {}
        raise AssertionError("Unexpected RPC: " + method)

    def events(self, thread_id):
        with self.lock:
            return self.queues.setdefault(thread_id, queue.Queue())

    def retire(self, thread_id):
        self.retired.append(thread_id)
        self.queues.pop(thread_id, None)

    def close(self):
        self.closed = True
        self._failure = CodexError("codex_process_exited", "synthetic close", 502)


class CodexClientTests(unittest.TestCase):
    def client(self, transport=None):
        transport = transport or ScriptedTransport()
        with patch("codex_client.resolve_codex_path", return_value="/fake/codex"):
            client = CodexClient()
        client._transport = transport
        client._directory = tempfile.TemporaryDirectory(prefix="test-codex-")
        client._config = {"model_provider": "openai"}
        client._permission = "test_translation_only"
        client.version = "0.153.4"
        self.addCleanup(client.close)
        return client, transport

    def test_only_final_answer_is_returned_and_usage_preserves_cache(self):
        def script(thread, turn, _):
            return [answer(thread, turn, "Do not show this", "commentary"),
                    answer(thread, turn, "first draft", item_id="final"),
                    answer(thread, turn, "最终译文", item_id="final"),
                    event("thread/tokenUsage/updated", thread, turn, tokenUsage={"last": {
                        "inputTokens": 50, "outputTokens": 20, "cachedInputTokens": 30,
                        "reasoningOutputTokens": 4}}), completed(thread, turn)]
        client, transport = self.client(ScriptedTransport(script))
        result = client.translate("source", model="gpt-6-luna")
        self.assertEqual(result.text, "最终译文")
        self.assertEqual(result.usage, {"prompt_tokens": 50, "completion_tokens": 20,
                         "cache_hit_tokens": 30, "cache_miss_tokens": 20, "reasoning_tokens": 4})
        self.assertFalse(transport.closed)
        self.assertEqual(transport.retired, ["thread-1"])
        self.assertNotIn("turn/interrupt", [method for method, _ in transport.calls])

    def test_final_items_in_completed_event_and_unknown_usage(self):
        client, _ = self.client(ScriptedTransport(lambda t, u, _: [completed(t, u, items=[{
            "id": "final", "type": "agentMessage", "phase": "final_answer", "text": "结果"}])]))
        result = client.translate("source", model="gpt-6-luna")
        self.assertEqual(result.text, "结果")
        self.assertEqual(result.usage, {})

    def test_previous_turn_output_and_usage_do_not_leak(self):
        def script(thread, turn, _):
            return [answer(thread, "old-turn", "stale"),
                    event("thread/tokenUsage/updated", thread, "old-turn",
                          tokenUsage={"last": {"inputTokens": 999}}),
                    completed(thread, "old-turn"), answer(thread, turn), completed(thread, turn)]
        client, _ = self.client(ScriptedTransport(script))
        result = client.translate("source", model="gpt-6-luna")
        self.assertEqual(result.text, "译文")
        self.assertEqual(result.usage, {})

    def test_concurrent_requests_keep_separate_threads_and_results(self):
        barrier = threading.Barrier(2)
        def script(thread, turn, params):
            barrier.wait(timeout=2)
            return [answer(thread, turn, params["input"][0]["text"]), completed(thread, turn)]
        client, transport = self.client(ScriptedTransport(script))
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(client.translate, text, model="gpt-6-luna") for text in ("first", "second")]
            self.assertEqual([f.result(timeout=3).text for f in futures], ["first", "second"])
        self.assertEqual(set(transport.retired), {"thread-1", "thread-2"})
        starts = [p for m, p in transport.calls if m == "thread/start"]
        self.assertTrue(all(p["ephemeral"] and p["allowProviderModelFallback"] is False for p in starts))

    def test_completion_without_final_answer_is_not_success(self):
        client, transport = self.client(ScriptedTransport(lambda t, u, _: [
            answer(t, u, "commentary", "commentary"), completed(t, u)]))
        with self.assertRaises(CodexError) as raised:
            client.translate("source", model="gpt-6-luna")
        self.assertEqual(raised.exception.code, "codex_protocol_error")
        self.assertFalse(transport.closed)

    def test_failed_turn_discards_any_partial_answer_and_redacts_error(self):
        client, _ = self.client(ScriptedTransport(lambda t, u, _: [answer(t, u, "partial"),
            completed(t, u, "failed", error={"message": "quota exceeded PRIVATE_SOURCE"})]))
        with self.assertRaises(CodexError) as raised:
            client.translate("source", model="gpt-6-luna")
        self.assertEqual(raised.exception.code, "codex_quota_exhausted")
        self.assertNotIn("PRIVATE_SOURCE", str(raised.exception))

    def test_terminal_error_notification_is_reported_without_waiting_for_timeout(self):
        client, _ = self.client(ScriptedTransport(lambda t, u, _: [
            event("error", t, u, error={"message": "quota exceeded PRIVATE_SOURCE"}, willRetry=False)]))
        with self.assertRaises(CodexError) as raised:
            client.translate("source", model="gpt-6-luna", timeout=0.1)
        self.assertEqual(raised.exception.code, "codex_quota_exhausted")
        self.assertNotIn("PRIVATE_SOURCE", str(raised.exception))

    def test_retry_notification_can_be_followed_by_success(self):
        client, _ = self.client(ScriptedTransport(lambda t, u, _: [
            event("error", t, u, error={"message": "temporary network error"}, willRetry=True),
            answer(t, u), completed(t, u)]))
        self.assertEqual(client.translate("source", model="gpt-6-luna").text, "译文")

    def test_cancel_after_start_interrupts_and_unsubscribes(self):
        client, transport = self.client(ScriptedTransport(lambda *_: []))
        def cancelled():
            if transport.turn_started.is_set():
                raise RuntimeError("user cancelled")
        with self.assertRaisesRegex(RuntimeError, "user cancelled"):
            client.translate("source", model="gpt-6-luna", check_cancelled=cancelled)
        methods = [m for m, _ in transport.calls]
        self.assertIn("turn/interrupt", methods)
        self.assertIn("thread/unsubscribe", methods)
        self.assertFalse(transport.closed)

    def test_cancel_after_thread_creation_before_turn_start_recycles_peer(self):
        client, transport = self.client()
        def cancelled():
            if transport.thread_counter:
                raise RuntimeError("user cancelled")
        with self.assertRaisesRegex(RuntimeError, "user cancelled"):
            client.translate("source", model="gpt-6-luna", check_cancelled=cancelled)
        self.assertFalse(transport.turn_started.is_set())
        self.assertTrue(transport.closed)
        self.assertEqual(transport.retired, ["thread-1"])
        self.assertEqual(client._active, 0)

    def test_cancellation_reaches_account_preparation(self):
        client, transport = self.client()
        original = transport.call
        def call(method, params, **kwargs):
            if method == "account/read":
                self.assertTrue(callable(kwargs.get("check_cancelled")))
                raise RuntimeError("cancelled preparing")
            return original(method, params, **kwargs)
        transport.call = call
        with self.assertRaisesRegex(RuntimeError, "cancelled preparing"):
            client.translate("source", model="gpt-6-luna")
        self.assertFalse(transport.turn_started.is_set())
        self.assertEqual(client._active, 0)

    def test_cleanup_failure_still_releases_concurrency_slot(self):
        client, transport = self.client()
        original = transport.call
        def call(method, params, **kwargs):
            if method == "thread/unsubscribe":
                raise RuntimeError("synthetic cleanup failure")
            return original(method, params, **kwargs)
        transport.call = call
        with self.assertRaisesRegex(RuntimeError, "cleanup failure"):
            client.translate("source", model="gpt-6-luna")
        self.assertEqual(client._active, 0)
        self.assertTrue(client._slots.acquire(blocking=False))
        self.assertTrue(client._slots.acquire(blocking=False))
        self.assertFalse(client._slots.acquire(blocking=False))
        client._slots.release()
        client._slots.release()

    def test_timeout_interrupts_and_recycles_unresponsive_owned_peer(self):
        client, transport = self.client(ScriptedTransport(lambda *_: []))
        transport.interrupt_acknowledged = False
        with self.assertRaises(CodexError) as raised:
            client.translate("source", model="gpt-6-luna", timeout=0.025)
        self.assertEqual(raised.exception.code, "codex_timeout")
        self.assertTrue(transport.closed)
        self.assertIsNone(client._transport)
        self.assertEqual(client._active, 0)

    def test_cancel_reclaims_peer_and_slots_while_another_thread_holds_client_lock(self):
        client, transport = self.client(ScriptedTransport(lambda *_: []))
        transport.interrupt_acknowledged = False
        cancel = threading.Event()
        lock_held = threading.Event()
        release_lock = threading.Event()
        outcomes = queue.Queue()

        def check_cancelled():
            if cancel.is_set():
                raise RuntimeError("user cancelled")

        def translate():
            try:
                outcomes.put(client.translate("source", model="gpt-6-luna", check_cancelled=check_cancelled))
            except Exception as error:
                outcomes.put(error)

        def hold_preparation_lock():
            with client._lock:
                lock_held.set()
                release_lock.wait(timeout=5)

        worker = threading.Thread(target=translate, daemon=True)
        holder = threading.Thread(target=hold_preparation_lock, daemon=True)
        worker.start()
        try:
            self.assertTrue(transport.turn_started.wait(timeout=2))
            holder.start()
            self.assertTrue(lock_held.wait(timeout=2))
            cancel.set()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive(), "Cancellation waited for the unrelated preparation lock")
            self.assertFalse(release_lock.is_set())
            self.assertTrue(holder.is_alive(), "The competing operation must still own the lock")
            self.assertTrue(transport.closed)
            self.assertRegex(str(outcomes.get_nowait()), "user cancelled")
            self.assertEqual(client._active, 0)
            self.assertTrue(client._slots.acquire(blocking=False))
            self.assertTrue(client._slots.acquire(blocking=False))
            self.assertFalse(client._slots.acquire(blocking=False))
            client._slots.release()
            client._slots.release()
        finally:
            release_lock.set()
            cancel.set()
            if holder.ident is not None:
                holder.join(timeout=2)
            worker.join(timeout=2)

    def test_tool_item_stops_generation(self):
        client, transport = self.client(ScriptedTransport(lambda t, u, _: [
            event("item/started", t, u, item={"id": "tool", "type": "commandExecution"})]))
        with self.assertRaises(CodexError) as raised:
            client.translate("source", model="gpt-6-luna")
        self.assertEqual(raised.exception.code, "codex_isolation_failed")
        self.assertIn("turn/interrupt", [m for m, _ in transport.calls])

    def test_thread_configuration_mismatch_closes_peer_before_generation(self):
        for override in ({"instructionSources": ["user-file"]},
                         {"sandbox": {"type": "readOnly", "networkAccess": True}},
                         {"model": "unexpected-model"}, {"serviceTier": "fast"}):
            with self.subTest(override=override):
                client, transport = self.client()
                transport.thread_override = override
                with self.assertRaises(CodexError) as raised:
                    client.translate("source", model="gpt-6-luna")
                self.assertEqual(raised.exception.code, "codex_isolation_failed")
                self.assertTrue(transport.closed)
                self.assertFalse(transport.turn_started.is_set())

    def test_login_model_and_effort_validation_never_generate(self):
        for account, model, effort, expected in [
            (None, "gpt-6-luna", None, "codex_not_logged_in"),
            ({"type": "apiKey"}, "gpt-6-luna", None, "codex_not_logged_in"),
            ({"type": "chatgpt"}, "unavailable", None, "codex_model_unavailable"),
            ({"type": "chatgpt"}, "gpt-6-luna", "ultra", "codex_invalid_reasoning"),
        ]:
            with self.subTest(expected=expected):
                client, transport = self.client()
                transport.account = account
                with self.assertRaises(CodexError) as raised:
                    client.translate("source", model=model, reasoning_effort=effort)
                self.assertEqual(raised.exception.code, expected)
                self.assertFalse(transport.turn_started.is_set())

    def test_model_default_and_explicit_none_effort_are_preserved(self):
        client, transport = self.client()
        self.assertEqual(client.check_ready("gpt-6-luna")["reasoningEffort"], "medium")
        self.assertEqual(client.check_ready("gpt-6-luna", "none")["reasoningEffort"], "none")
        self.assertEqual(sum(m == "model/list" for m, _ in transport.calls), 1)

    def test_expired_login_discards_cached_auth_manager_for_user_retry(self):
        client, transport = self.client(ScriptedTransport(lambda t, u, _: [
            completed(t, u, "failed", error={"message": "401 unauthorized"})]))
        with self.assertRaises(CodexError) as raised:
            client.translate("source", model="gpt-6-luna")
        self.assertEqual(raised.exception.code, "codex_not_logged_in")
        self.assertTrue(transport.closed)
        self.assertIsNone(client._transport)


class CodexTransportTests(unittest.TestCase):
    def peer(self, code):
        transport = _Transport([sys.executable, "-u", "-c", code], tempfile.gettempdir(), dict(os.environ))
        self.addCleanup(transport.close)
        return transport

    def test_owned_windows_job_is_closed_even_if_process_stop_reports_error(self):
        for fail_after_stop in (False, True):
            with self.subTest(fail_after_stop=fail_after_stop):
                job = Mock()
                with patch("codex_client._owned_windows_job", return_value=job) as assign:
                    transport = self.peer("import sys; sys.stdin.readline()")
                assign.assert_called_once_with(transport.process)
                if fail_after_stop:
                    stop_process = codex_client._stop_process
                    def stop_then_fail(process):
                        stop_process(process)
                        raise OSError("synthetic failure after termination")
                    with patch("codex_client._stop_process", side_effect=stop_then_fail):
                        with self.assertRaisesRegex(OSError, "synthetic failure"):
                            transport.close()
                else:
                    transport.close()
                job.close.assert_called_once()
                self.assertIsNotNone(transport.process.poll())
                self.assertTrue(transport.process.stdin.closed)
                self.assertTrue(transport.process.stdout.closed)

    def test_failed_windows_job_assignment_stops_peer_before_any_rpc(self):
        spawned = []
        def reject_job(process):
            spawned.append(process)
            self.addCleanup(codex_client._stop_process, process)
            raise OSError("synthetic job assignment failure")
        with patch("codex_client._owned_windows_job", side_effect=reject_job), \
                patch.object(_Transport, "_write") as write:
            with self.assertRaises(CodexError) as raised:
                _Transport([sys.executable, "-u", "-c", "import sys; sys.stdin.readline()"],
                           tempfile.gettempdir(), dict(os.environ))
        self.assertEqual(raised.exception.code, "codex_isolation_failed")
        write.assert_not_called()
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].poll())
        self.assertTrue(spawned[0].stdin.closed)
        self.assertTrue(spawned[0].stdout.closed)

    def test_out_of_order_rpc_replies_are_correlated_by_id(self):
        transport = self.peer("""
import json, sys
requests = [json.loads(sys.stdin.readline()) for _ in range(2)]
for request in reversed(requests):
    print(json.dumps({'id': request['id'], 'result': {'echo': request['method']}}), flush=True)
sys.stdin.readline()
""")
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(transport.call, "first", {}, timeout=3)
            second = executor.submit(transport.call, "second", {}, timeout=3)
            self.assertEqual(first.result(timeout=4), {"echo": "first"})
            self.assertEqual(second.result(timeout=4), {"echo": "second"})

    def test_notification_queues_are_isolated_and_retired_threads_ignored(self):
        transport = self.peer("""
import json, sys
request = json.loads(sys.stdin.readline())
for thread in ('retired', 'thread-b', 'thread-a'):
    print(json.dumps({'method': 'item/completed', 'params': {'threadId': thread, 'item': {'text': thread}}}), flush=True)
print(json.dumps({'id': request['id'], 'result': {}}), flush=True)
sys.stdin.readline()
""")
        transport.retire("retired")
        transport.call("send-events", {}, timeout=3)
        self.assertEqual(transport.events("thread-a").get_nowait()["params"]["item"]["text"], "thread-a")
        self.assertEqual(transport.events("thread-b").get_nowait()["params"]["item"]["text"], "thread-b")
        self.assertNotIn("retired", transport._events)

    def test_interactive_server_request_is_rejected(self):
        transport = self.peer("""
import json, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({'id': 99, 'method': 'item/commandExecution/requestApproval', 'params': {'threadId': 'translation'}}), flush=True)
rejection = json.loads(sys.stdin.readline())
print(json.dumps({'id': request['id'], 'result': rejection}), flush=True)
sys.stdin.readline()
""")
        result = transport.call("probe", {}, timeout=3)
        self.assertEqual(result["id"], 99)
        self.assertEqual(result["error"]["code"], -32601)
        failure = transport.events("translation").get_nowait()
        self.assertEqual(failure.code, "codex_isolation_failed")

    def test_malformed_json_and_process_exit_fail_pending_request(self):
        for code, expected in [
            ("import sys; sys.stdin.readline(); print('not-json', flush=True)", "codex_protocol_error"),
            ("import sys; sys.stdin.readline()", "codex_process_exited"),
        ]:
            with self.subTest(expected=expected):
                transport = self.peer(code)
                with self.assertRaises(CodexError) as raised:
                    transport.call("probe", {}, timeout=3)
                self.assertEqual(raised.exception.code, expected)
                self.assertEqual(transport._pending, {})

    def test_process_exit_wakes_all_pending_calls(self):
        transport = self.peer("import sys; sys.stdin.readline(); sys.stdin.readline()")
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = [executor.submit(transport.call, name, {}, timeout=3) for name in ("first", "second")]
            for result in pending:
                with self.assertRaises(CodexError) as raised:
                    result.result(timeout=4)
                self.assertEqual(raised.exception.code, "codex_process_exited")
        self.assertEqual(transport._pending, {})

    def test_rpc_timeout_removes_pending_request(self):
        transport = self.peer("import sys; sys.stdin.readline(); sys.stdin.readline()")
        with self.assertRaises(CodexError) as raised:
            transport.call("probe", {}, timeout=0.025)
        self.assertEqual(raised.exception.code, "codex_timeout")
        self.assertEqual(transport._pending, {})

    def test_blocked_stdin_does_not_block_timeout_or_cancellation(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                transport = self.peer("import time; time.sleep(60)")
                outcomes = queue.Queue()
                cancelled = threading.Event()

                def check():
                    if cancelled.is_set():
                        raise RuntimeError("user cancelled")

                def call():
                    try:
                        outcomes.put(transport.call("large-prompt", {"text": "x" * 4_000_000},
                            timeout=5 if cancel else 0.1, check_cancelled=check))
                    except Exception as error:
                        outcomes.put(error)

                worker = threading.Thread(target=call, daemon=True)
                worker.start()
                if cancel:
                    cancelled.set()
                try:
                    result = outcomes.get(timeout=2)
                    if cancel:
                        self.assertRegex(str(result), "user cancelled")
                    else:
                        self.assertIsInstance(result, CodexError)
                        self.assertEqual(result.code, "codex_timeout")
                    self.assertEqual(transport._pending, {})
                finally:
                    transport.close()
                    worker.join(timeout=2)
                self.assertIsNotNone(transport.process.poll())
                self.assertFalse(transport._writer.is_alive())
                self.assertFalse(worker.is_alive())

    def test_rpc_errors_are_classified_without_echoing_sensitive_content(self):
        for message, expected in [("401 PRIVATE_SOURCE", "codex_not_logged_in"),
                                  ("model unavailable PRIVATE_SOURCE", "codex_model_unavailable"),
                                  ("invalid params PRIVATE_SOURCE", "codex_incompatible")]:
            with self.subTest(expected=expected):
                error = codex_client._error_from_rpc({"message": message})
                self.assertEqual(error.code, expected)
                self.assertNotIn("PRIVATE_SOURCE", str(error))


class CodexStartupTests(unittest.TestCase):
    def test_preparation_timeout_applies_even_with_caller_cancellation_check(self):
        caller_check = Mock()
        with patch("codex_client.time.monotonic", side_effect=[100, 129.9, 130]):
            bounded = codex_client._preparation_check(caller_check)
            bounded()
            with self.assertRaises(CodexError) as raised:
                bounded()
        self.assertEqual(raised.exception.code, "codex_timeout")
        self.assertEqual(caller_check.call_count, 2)
        cancelled = codex_client._preparation_check(Mock(side_effect=RuntimeError("cancelled")))
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            cancelled()

    def test_version_poll_cancellation_terminates_the_owned_child(self):
        real_popen = subprocess.Popen
        spawned = []
        def slow_version(*args, **kwargs):
            process = real_popen([sys.executable, "-u", "-c", "import time; time.sleep(30)"],
                                 **kwargs)
            spawned.append(process)
            self.addCleanup(codex_client._stop_process, process)
            return process
        caller_check = Mock(side_effect=[None, RuntimeError("cancelled version check")])
        with patch("codex_client.subprocess.Popen", side_effect=slow_version):
            with self.assertRaisesRegex(RuntimeError, "cancelled version check"):
                codex_client._read_version("/fake/codex", caller_check)
        self.assertEqual(caller_check.call_count, 2)
        self.assertIsNotNone(spawned[0].poll())
        self.assertTrue(spawned[0].stdout.closed)

    def test_old_cli_is_rejected_before_app_server_start(self):
        with patch("codex_client.resolve_codex_path", return_value="/fake/codex"):
            client = CodexClient()
        self.addCleanup(client.close)
        process = Mock()
        process.communicate.return_value = ("codex-cli 0.153.3", None)
        process.returncode = 0
        process.poll.return_value = 0
        with patch("codex_client.subprocess.Popen", return_value=process), \
                patch.object(client, "_launch") as launch:
            with self.assertRaises(CodexError) as raised:
                client.list_models()
        self.assertEqual(raised.exception.code, "codex_incompatible")
        launch.assert_not_called()

    def test_mcp_names_that_cannot_be_overridden_are_refused(self):
        with patch("codex_client.resolve_codex_path", return_value="/fake/codex"):
            client = CodexClient()
        self.addCleanup(client.close)
        bootstrap = Mock()
        bootstrap.call.return_value = {"config": {"mcp_servers": {"name.with.dot": {"enabled": True}}}}
        with patch("codex_client._read_version", return_value="0.153.4"), \
                patch.object(client, "_launch", return_value=bootstrap) as launch:
            with self.assertRaises(CodexError) as raised:
                client.list_models()
        self.assertEqual(raised.exception.code, "codex_isolation_failed")
        bootstrap.close.assert_called_once()
        self.assertEqual(launch.call_count, 1)

    def test_effective_configuration_cannot_silently_enable_tools(self):
        with patch("codex_client.resolve_codex_path", return_value="/fake/codex"):
            client = CodexClient()
        self.addCleanup(client.close)
        bootstrap, runtime = Mock(), Mock()
        bootstrap.call.return_value = {"config": {"mcp_servers": {}}}
        runtime.call.return_value = {"config": {"features": {"shell_tool": True}}}
        with patch("codex_client._read_version", return_value="0.153.4"), \
                patch.object(client, "_launch", side_effect=[bootstrap, runtime]):
            with self.assertRaises(CodexError) as raised:
                client.list_models()
        self.assertEqual(raised.exception.code, "codex_isolation_failed")
        bootstrap.close.assert_called_once()
        runtime.close.assert_called_once()
        self.assertIsNone(client._directory)

    def test_launch_preserves_auth_home_but_removes_api_route_overrides(self):
        with patch("codex_client.resolve_codex_path", return_value="/fake/codex"):
            client = CodexClient()
        client._directory = tempfile.TemporaryDirectory()
        self.addCleanup(client.close)
        with patch.dict(os.environ, {"CODEX_HOME": "/existing/account", "OPENAI_API_KEY": "secret", "CODEX_API_KEY": "secret", "OPENAI_BASE_URL": "https://invalid.test"}), \
                patch("codex_client._Transport") as transport:
            client._launch({})
        env = transport.call_args.args[2]
        self.assertEqual(env["CODEX_HOME"], "/existing/account")
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
            self.assertNotIn(key, env)


class CodexPathTests(unittest.TestCase):
    def test_npm_windows_wrapper_selects_native_matching_architecture(self):
        with tempfile.TemporaryDirectory(prefix="Codex path with spaces ") as directory:
            root = Path(directory)
            wrapper = root / "codex.cmd"
            wrapper.write_text("@echo wrapper must never execute", encoding="utf-8")
            for architecture in ("x86_64", "aarch64"):
                native = root / "node_modules" / "@openai" / "codex" / "vendor" / (architecture + "-pc-windows-msvc") / "codex" / "codex.exe"
                native.parent.mkdir(parents=True)
                native.write_bytes(b"fake binary")
            with patch.dict(os.environ, {"PROCESSOR_ARCHITECTURE": "ARM64"}), \
                    patch("codex_client.shutil.which", return_value=None):
                found = resolve_codex_path(str(wrapper))
            self.assertIn("aarch64", found)
            self.assertTrue(found.endswith("codex.exe"))

    def test_windows_wrapper_without_native_binary_is_not_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            wrapper = Path(directory) / "codex.cmd"
            wrapper.write_text("echo must not execute", encoding="utf-8")
            with patch("codex_client.shutil.which", return_value=None), patch("codex_client.subprocess.Popen") as launch:
                with self.assertRaises(CodexError) as raised:
                    resolve_codex_path(str(wrapper))
            self.assertEqual(raised.exception.code, "codex_not_installed")
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
