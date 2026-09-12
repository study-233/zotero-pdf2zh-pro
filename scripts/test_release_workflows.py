"""Exercise the actual inline guards used when reusing release packages."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def inline(workflow, marker):
    text = (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
    return textwrap.dedent(text.split("<<'" + marker + "'\n", 1)[1].split("\n          " + marker, 1)[0])


class WorkflowGateTests(unittest.TestCase):
    def execute(self, workflow, marker, documents, args, ancestor=True):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for i, document in enumerate(documents):
                path = Path(directory) / f"{i}.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                paths.append(str(path))
            with patch.object(sys, "argv", ["guard", *paths, *args]), patch("subprocess.run") as git:
                if not ancestor:
                    git.side_effect = subprocess.CalledProcessError(1, "git")
                exec(compile(inline(workflow, marker), marker, "exec"), {})
                return git.call_args

    def test_reuse_requires_matching_completed_build_and_successful_packaging(self):
        run = {"head_sha": SHA, "status": "completed", "path": ".github/workflows/build-windows-release.yml"}
        jobs = {"jobs": [{"steps": [{"name": "Build artifacts and verify Windows startup", "conclusion": "success"}]}]}
        self.execute("build-windows-release.yml", "PY_RUN", [run, jobs], [SHA])
        for field, value in [("head_sha", "b" * 40), ("status", "in_progress"), ("path", "other.yml")]:
            with self.assertRaises(AssertionError):
                self.execute("build-windows-release.yml", "PY_RUN", [{**run, field: value}, jobs], [SHA])
        jobs["jobs"][0]["steps"][0]["conclusion"] = "failure"
        with self.assertRaises(AssertionError):
            self.execute("build-windows-release.yml", "PY_RUN", [run, jobs], [SHA])

    def test_publication_allows_descendant_workflow_but_rejects_wrong_source_and_failed_validation(self):
        run = {"head_sha": "b" * 40, "status": "completed", "conclusion": "success",
               "path": ".github/workflows/build-windows-release.yml", "display_title": f"Build v1.6.9 at {SHA}"}
        call = self.execute("publish-pypi.yml", "PY_VERIFY_RUN", [run], [SHA, "1.6.9"])
        self.assertEqual(call.args[0], ["git", "merge-base", "--is-ancestor", SHA, "b" * 40])
        for field, value in [("display_title", "Build wrong source"), ("conclusion", "failure"), ("path", "other.yml")]:
            with self.assertRaises(AssertionError):
                self.execute("publish-pypi.yml", "PY_VERIFY_RUN", [{**run, field: value}], [SHA, "1.6.9"])
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute("publish-pypi.yml", "PY_VERIFY_RUN", [run], [SHA, "1.6.9"], ancestor=False)

    def test_standard_requires_successful_push_ci_for_same_commit(self):
        good = {"head_sha": SHA, "conclusion": "success", "event": "push"}
        self.execute("build-windows-release.yml", "PY_CORE", [{"workflow_runs": [good]}], [SHA])
        for runs in [[], [{**good, "head_sha": "b" * 40}], [{**good, "event": "pull_request"}]]:
            with self.assertRaises(AssertionError):
                self.execute("build-windows-release.yml", "PY_CORE", [{"workflow_runs": runs}], [SHA])


if __name__ == "__main__":
    unittest.main()
