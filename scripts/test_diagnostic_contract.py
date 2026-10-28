"""Exercise feature-registration guards and the frozen released format."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from generate_diagnostic_contract import ROOT, SOURCE, render, validate


class DiagnosticContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((ROOT / SOURCE).read_text(encoding="utf-8"))

    def test_generated_boundaries_are_current(self):
        validate(self.contract)
        for path, content in render(self.contract).items():
            self.assertEqual((ROOT / path).read_text(encoding="utf-8"), content, str(path))

    def test_new_feature_requires_a_diagnostic_coverage_declaration(self):
        del self.contract['productCapabilities']['taskRecovery']
        with self.assertRaisesRegex(ValueError, 'productCapabilities'):
            validate(self.contract)

    def test_new_validation_error_requires_registration(self):
        original = Path.read_text
        def source(path, *args, **kwargs):
            text = original(path, *args, **kwargs)
            if path == ROOT / 'server/pdf2zh_pro/translator/validation.py':
                text += '\nraise InvalidTranslation("new_feature_failure")\n'
            return text
        with patch.object(Path, 'read_text', source), self.assertRaisesRegex(ValueError, 'Unregistered validation'):
            validate(self.contract)

    def test_breaking_v1_changes_and_raw_fields_are_rejected(self):
        for mutate in (
            lambda c: c['enums']['protocol'].remove('chat_completions'),
            lambda c: c['numeric'].remove('page'),
            lambda c: c['strings'].append('prompt'),
            lambda c: c.update(supportedSchemaVersions=[1, 2]),
            lambda c: c['providerErrors'].pop('chatgpt_usage_limit'),
        ):
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(self.contract)
                mutate(changed)
                with self.assertRaises(ValueError):
                    validate(changed)

    def test_new_server_event_requires_registration(self):
        original = Path.read_text
        def source(path, *args, **kwargs):
            text = original(path, *args, **kwargs)
            if path == ROOT / 'server/server.py':
                text += '\nTASK_MANAGER.diagnostics.record("new_feature_event", {})\n'
            return text
        with patch.object(Path, 'read_text', source), self.assertRaisesRegex(ValueError, 'Unregistered diagnostic event'):
            validate(self.contract)

    def test_evidence_fields_links_and_rules_require_registration(self):
        for mutate in (
            lambda c: c['evidenceFields']['request'].append('rawResponse'),
            lambda c: c['identifierLists'].update(callRefs='unknownRef'),
            lambda c: c['diagnosisRules'].pop('validation_failed'),
        ):
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(self.contract)
                mutate(changed)
                with self.assertRaises(ValueError):
                    validate(changed)

    def test_generated_module_has_no_runtime_dependencies_and_is_packaged(self):
        module = ROOT / 'server/diagnostic_contract.py'
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / module.name
            target.write_bytes(module.read_bytes())
            result = subprocess.run([sys.executable, '-I', '-c',
                'import runpy,sys; c=runpy.run_path(sys.argv[1])["CONTRACT"]; assert c["schemaVersion"]==1', str(target)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"diagnostic_contract"', (ROOT / 'server/pyproject.toml').read_text(encoding="utf-8"))
        self.assertIn('server/diagnostic_contract.py', (ROOT / 'server/Dockerfile').read_text(encoding="utf-8"))
        self.assertIn('"diagnostic_evidence"', (ROOT / 'server/pyproject.toml').read_text(encoding="utf-8"))
        self.assertIn('server/diagnostic_evidence.py', (ROOT / 'server/Dockerfile').read_text(encoding="utf-8"))


if __name__ == '__main__':
    unittest.main()
