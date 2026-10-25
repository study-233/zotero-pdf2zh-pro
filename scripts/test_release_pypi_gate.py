"""The installer gate uses the shared exact-file PyPI verifier."""
from pathlib import Path
import unittest
import test_pypi_publication as fixtures
import pypi_publication as gate


class PublicPyPIGateTests(unittest.TestCase):
    def test_partial_release_does_not_hide_a_conflicting_file(self):
        fixture = fixtures.PublicationTests()
        fixture.setUp()
        data, _ = fixture.fetch('json', 10)
        data['urls'].pop(0)
        data['urls'][0]['digests']['sha256'] = '0'*64
        with self.assertRaises(gate.PublicationConflict):
            gate.inspect_files(data['urls'], gate.expected_files(fixture.manifest))

    def test_local_and_cloud_use_shared_checker_and_separate_visibility_job(self):
        root = Path(__file__).resolve().parents[1]
        shell = (root/'scripts/release.sh').read_text()
        workflow = (root/'.github/workflows/publish-pypi.yml').read_text()
        self.assertIn('scripts/pypi_publication.py dist/release/checksums.json --timeout 0', shell)
        self.assertIn('scripts/pypi_publication.py', workflow)
        visibility = workflow.split('  verify-public:', 1)[1].split('  github-release:', 1)[0]
        self.assertIn('needs: publish', visibility)
        self.assertNotIn('uv publish', visibility)
        self.assertNotIn('id-token: write', visibility)
        github = workflow.split('  github-release:', 1)[1]
        self.assertIn('needs: [publish, verify-public]', github)
        self.assertNotIn('id-token: write', github)
        self.assertEqual(workflow.count('id-token: write'), 1)


if __name__ == '__main__':
    unittest.main()
