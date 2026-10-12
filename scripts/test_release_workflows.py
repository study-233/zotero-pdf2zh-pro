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
    def test_source_gate_rejects_non_main_invalid_sha_and_non_ancestor(self):
        good = ["refs/heads/main", SHA, "b" * 40]
        self.execute("build-windows-release.yml", "PY_SOURCE", [], good)
        for args in [["refs/heads/topic", *good[1:]], [good[0], "main", good[2]]]:
            with self.assertRaises(AssertionError):
                self.execute("build-windows-release.yml", "PY_SOURCE", [], args)
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute("build-windows-release.yml", "PY_SOURCE", [], good, ancestor=False)

    def test_source_and_ci_gates_precede_target_checkout_and_all_caches(self):
        text = (ROOT / ".github/workflows/build-windows-release.yml").read_text(encoding="utf-8")
        gate = text.index("<<'PY_SOURCE'")
        ci = text.index("<<'PY_CORE'")
        checkout = text.index('git switch -C main "$RELEASE_COMMIT"')
        self.assertLess(gate, ci)
        self.assertLess(ci, checkout)
        for marker in ["uses: astral-sh/setup-uv", "uses: Swatinem/rust-cache",
                       "uses: actions/cache@", "run: uv run", "bash scripts/release.sh"]:
            self.assertLess(checkout, text.index(marker))
        self.assertIn("ref: ${{ github.sha }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("ref: ${{ inputs.commit }}", text)

    def execute(self, workflow, marker, documents, args, ancestor=True, harness_changed=False):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for i, document in enumerate(documents):
                path = Path(directory) / f"{i}.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                paths.append(str(path))
            with patch.object(sys, "argv", ["guard", *paths, *args]), patch("subprocess.run") as git:
                if not ancestor:
                    git.side_effect = subprocess.CalledProcessError(1, "git")
                elif harness_changed:
                    def check_git(command, **kwargs):
                        if command[1] == "diff":
                            raise subprocess.CalledProcessError(1, command)
                    git.side_effect = check_git
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

    def test_collection_recovery_requires_all_passed_tests_and_unchanged_harness(self):
        run = {"status": "completed", "conclusion": "failure", "head_sha": "b" * 40,
               "path": ".github/workflows/build-windows-release.yml", "display_title": f"Build v1.9.0 at {SHA}"}
        names = ["Verify committed versions", "Require successful core CI for the release commit",
                 "Restore packages from a completed artifact build", "Use current test harness for reused artifacts",
                 "Fresh installed Python 3.13 runtime and OCR", "Verify installed startup and upgrade",
                 "Verify real downloads and progress with the release controller"]
        steps = [{"name": name, "conclusion": "success"} for name in names]
        steps.append({"name": "Verify and collect release artifacts", "conclusion": "failure"})
        jobs = {"jobs": [{"name": "build", "steps": steps}]}
        args = [SHA, "1.9.0", "c" * 40]
        call = self.execute("build-windows-release.yml", "PY_VALIDATION", [run, jobs], args)
        self.assertEqual(call.args[0][:5], ["git", "diff", "--exit-code", "b" * 40, "c" * 40])
        for step in steps[:-1]:
            step["conclusion"] = "failure"
            with self.assertRaises(AssertionError):
                self.execute("build-windows-release.yml", "PY_VALIDATION", [run, jobs], args)
            step["conclusion"] = "success"
        for field, value in [("display_title", "wrong source"), ("status", "in_progress"), ("path", "other.yml")]:
            with self.assertRaises(AssertionError):
                self.execute("build-windows-release.yml", "PY_VALIDATION", [{**run, field: value}, jobs], args)
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute("build-windows-release.yml", "PY_VALIDATION", [run, jobs], args, ancestor=False)
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute("build-windows-release.yml", "PY_VALIDATION", [run, jobs], args, harness_changed=True)


if __name__ == "__main__":
    unittest.main()
