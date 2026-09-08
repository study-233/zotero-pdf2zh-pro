import unittest
from unittest.mock import patch

import httpx

from provider_models import ModelDiscoveryError, list_provider_models


class ModelDiscoveryTests(unittest.TestCase):
    def response(self, status=200, payload=None):
        return httpx.Response(status, json=payload if payload is not None else {"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}]})

    def test_base_full_endpoints_and_custom_paths(self):
        for path in ["/custom/v1", "/custom/v1/", "/custom/v1/chat/completions", "/custom/v1/responses", ""]:
            with self.subTest(path=path), patch("provider_models.httpx.get", return_value=self.response()) as get:
                self.assertEqual(list_provider_models({"apiUrl": "https://relay.invalid" + path, "apiKey": "TEST_SECRET"}), ["a", "b"])
                expected = "https://relay.invalid/custom/v1/models" if path else "https://relay.invalid/models"
                self.assertEqual(get.call_args.args[0], expected)
                self.assertEqual(get.call_args.kwargs["headers"], {"Authorization": "Bearer TEST_SECRET"})
                self.assertFalse(get.call_args.kwargs["follow_redirects"])

    def test_empty_list_valid_and_bad_ids_ignored(self):
        for payload in [{"data": []}, {"data": [{"id": None}, {"id": ""}, "bad"]}]:
            with patch("provider_models.httpx.get", return_value=self.response(payload=payload)):
                self.assertEqual(list_provider_models({"apiUrl": "https://relay.invalid/v1"}), [])

    def test_upstream_errors_are_useful_and_never_echo_secrets(self):
        for code, expected in [(401, "Key"), (403, "权限"), (404, "不支持"), (405, "不支持"), (429, "额度"), (500, "失败"), (302, "失败")]:
            with self.subTest(code=code), patch("provider_models.httpx.get", return_value=self.response(code, {"message": "TEST_SECRET"})):
                with self.assertRaises(ModelDiscoveryError) as error:
                    list_provider_models({"apiUrl": "https://relay.invalid", "apiKey": "TEST_SECRET"})
                self.assertIn(expected, str(error.exception)); self.assertNotIn("TEST_SECRET", str(error.exception))

    def test_network_timeout_and_malformed_response(self):
        for error, expected in [(httpx.ReadTimeout("TEST_SECRET"), "超时"), (httpx.ConnectError("TEST_SECRET"), "无法连接")]:
            with patch("provider_models.httpx.get", side_effect=error):
                with self.assertRaisesRegex(ModelDiscoveryError, expected):
                    list_provider_models({"apiUrl": "https://relay.invalid"})
        for payload in [[], {}, {"data": "bad"}]:
            with patch("provider_models.httpx.get", return_value=self.response(payload=payload)):
                with self.assertRaisesRegex(ModelDiscoveryError, "格式"):
                    list_provider_models({"apiUrl": "https://relay.invalid"})

    def test_input_validation_happens_before_network(self):
        for data in [{}, {"apiUrl": 123}, {"apiUrl": "file:///tmp"}, {"apiUrl": "https://a.invalid/v1?key=SECRET"}, {"apiUrl": "https://a.invalid", "apiKey": "key\n"}, {"apiUrl": "https://a.invalid/responses", "apiProtocol": "chat_completions"}]:
            with patch("provider_models.httpx.get") as get:
                with self.assertRaises(ModelDiscoveryError): list_provider_models(data)
                get.assert_not_called()

    def test_flask_contract_and_bad_json(self):
        import server
        client = server.create_app().test_client()
        with patch("server.list_provider_models", return_value=["model-a"]):
            response = client.post("/list-models", json={"apiUrl": "https://relay.invalid"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, {"status": "ok", "models": ["model-a"]})
        self.assertEqual(client.post("/list-models", json=[]).status_code, 400)
        with patch("server.list_provider_models", side_effect=ModelDiscoveryError("超时", 504)):
            self.assertEqual(client.post("/list-models", json={}).status_code, 504)
