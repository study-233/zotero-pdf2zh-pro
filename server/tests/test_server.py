from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import server as server_module


def build_pdf_payload() -> str:
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
    return "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode("ascii")


def task_snapshot(status: str = "queued") -> dict[str, object]:
    return {
        "taskId": "task-1",
        "fileName": "paper.pdf",
        "service": "openai",
        "outputModes": ["dual"],
        "status": status,
        "stage": None,
        "stageCurrent": 0,
        "stageTotal": 0,
        "stageProgress": 0,
        "overallProgress": 0,
        "error": None,
        "resultFiles": {},
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "canCancel": status in {"queued", "running"},
        "cancelRequested": False,
    }


class ServerRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = server_module.create_app().test_client()

    def test_health_contract_includes_degraded_workspace_state(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")
        self.assertTrue(response.json["workspace"]["writable"])
        self.assertIn("pdf2zhVersion", response.json)
        self.assertIn("total", response.json["tasks"])

        with (
            patch.object(server_module, "TRANSLATES_DIR", Path("/missing-parent/x")),
            patch.object(
                server_module.Path,
                "mkdir",
                side_effect=PermissionError("permission denied"),
            ),
            patch.object(server_module.shutil, "disk_usage") as disk_usage,
        ):
            disk_usage.return_value = SimpleNamespace(free=123)
            degraded = server_module.build_health_payload()
        self.assertEqual(degraded["status"], "degraded")
        self.assertFalse(degraded["workspace"]["writable"])

    def test_translate_contract_handles_success_and_bad_requests(self) -> None:
        with patch.object(
            server_module,
            "translate_pdf_request",
            return_value=(b"%PDF-1.4\n", "paper.dual.pdf", "dual"),
        ):
            response = self.client.post(
                "/translate",
                json={
                    "fileName": "paper.pdf",
                    "fileContent": build_pdf_payload(),
                    "outputMode": "dual",
                    "service": "openai",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-PDF2ZH-Output-Mode"], "dual")
        self.assertEqual(response.data, b"%PDF-1.4\n")

        bad_requests = (
            ({"fileName": "paper.pdf", "outputMode": "mono"}, "diagnostics"),
            (
                {
                    "fileName": "paper.pdf",
                    "fileContent": build_pdf_payload(),
                    "outputMode": "compare",
                },
                "message",
            ),
        )
        for payload, field in bad_requests:
            with self.subTest(field=field):
                rejected = self.client.post("/translate", json=payload)
                self.assertEqual(rejected.status_code, 400)
                self.assertIn(field, rejected.json)

    def test_task_lifecycle_routes_preserve_the_contract(self) -> None:
        with patch.object(
            server_module.TASK_MANAGER,
            "create_task",
            return_value=task_snapshot(),
        ):
            created = self.client.post(
                "/tasks",
                json={
                    "fileName": "paper.pdf",
                    "fileContent": build_pdf_payload(),
                    "outputModes": ["dual"],
                    "service": "openai",
                },
            )
        self.assertEqual(created.status_code, 202)
        self.assertEqual(created.json["task"]["taskId"], "task-1")

        operations = (
            ("get_task", task_snapshot("running"), "get", "/tasks/task-1", 200),
            ("retry_task", task_snapshot(), "post", "/tasks/task-1/retry", 202),
            ("delete_task", task_snapshot("failed"), "delete", "/tasks/task-1", 200),
        )
        for method, result, verb, path, expected_status in operations:
            with self.subTest(method=method):
                with patch.object(server_module.TASK_MANAGER, method, return_value=result):
                    response = getattr(self.client, verb)(path)
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json["task"]["taskId"], "task-1")

        with patch.object(
            server_module.TASK_MANAGER,
            "clear_failed_tasks",
            return_value=2,
        ):
            cleared = self.client.post("/tasks/clear-failed")
        self.assertEqual(cleared.json["deletedCount"], 2)

    def test_task_result_contract_handles_success_and_invalid_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "paper.dual.pdf"
            output_path.write_bytes(b"%PDF-1.4\n")
            with patch.object(
                server_module.TASK_MANAGER,
                "get_result_file",
                return_value=(
                    SimpleNamespace(status="completed", task_id="task-1"),
                    SimpleNamespace(
                        output_path=output_path,
                        filename=output_path.name,
                        output_mode="dual",
                    ),
                ),
            ):
                response = self.client.get("/tasks/task-1/result?mode=dual")
            try:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["X-PDF2ZH-Task-Id"], "task-1")
                self.assertEqual(response.data, b"%PDF-1.4\n")
            finally:
                response.close()

        rejected = self.client.get("/tasks/task-1/result?mode=compare")
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("outputMode", rejected.json["message"])

    def test_configuration_contract_forwards_options_and_validation(self) -> None:
        with patch.object(server_module, "validate_service_config") as validate:
            validate.return_value = SimpleNamespace(
                service="openai",
                model="gpt-4.1",
                status="ok",
                diagnostics=[],
                live_test={"enabled": True, "ok": True, "message": "你好"},
            )
            response = self.client.post(
                "/validate-config",
                json={
                    "service": "openai",
                    "liveTest": True,
                    "translateTableText": False,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["model"], "gpt-4.1")
        self.assertTrue(validate.call_args.args[0]["live_test"])
        self.assertFalse(validate.call_args.args[0]["translate_table_text"])

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            prepared = server_module.prepare_translation_request(
                {
                    "fileName": "paper.pdf",
                    "fileContent": build_pdf_payload(),
                    "outputModes": ["mono", "dual"],
                    "service": "openai",
                    "disableTermExtraction": True,
                    "skipTextChecks": True,
                    "translateTableText": False,
                    "skipReferences": True,
                },
                workspace,
            )
            self.assertEqual(prepared.output_modes, ["mono", "dual"])
            self.assertEqual(
                Path(prepared.request_payload["input_path"]),
                workspace / "paper.pdf",
            )
            self.assertTrue(prepared.request_payload["no_auto_extract_glossary"])
            self.assertTrue(prepared.request_payload["skip_text_checks"])
            self.assertFalse(prepared.request_payload["translate_table_text"])
            self.assertTrue(prepared.request_payload["skip_references"])


if __name__ == "__main__":
    unittest.main()
