"""Exercise the actual inline guards used when reusing release packages."""
import json
import os
from pathlib import Path
import shutil
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
    def test_workspace_overlay_restores_old_and_new_release_commits(self):
        from test_release_cleanup import BASH
        if not BASH or not shutil.which('git'):
            self.skipTest('Bash and Git required')
        text = (ROOT / '.github/workflows/build-windows-release.yml').read_text(encoding='utf-8')
        step = text.split('      - name: Restore source test scripts after validation\n', 1)[1].split('\n      - name:', 1)[0]
        script = textwrap.dedent(step.split('        run: |\n', 1)[1])
        import release_policy
        names = release_policy.policy()['harnessPaths']
        for helpers_existed in (False, True):
            with self.subTest(helpers_existed=helpers_existed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'scripts').mkdir()
                original = names if helpers_existed else names[:2]
                for name in original:
                    (root / name).write_text('original\n', encoding='utf-8')
                def git(*args):
                    return subprocess.run(['git', '-c', 'user.name=Workspace Test', '-c', 'user.email=test@example.invalid', *args],
                                          cwd=root, capture_output=True, check=True)
                git('init', '-q')
                git('add', 'scripts')
                git('commit', '-qm', 'Fixture release source')
                for name in names:
                    (root / name).write_text('new harness\n', encoding='utf-8')
                trusted = root / 'renderer-harness'
                trusted.mkdir()
                for name in ('release_policy.py', 'release-policy.json'):
                    shutil.copyfile(ROOT/'scripts'/name, trusted/name)
                environment = dict(os.environ, RUNNER_TEMP=str(root), PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH'])
                result = subprocess.run([BASH, '--noprofile', '--norc'], input=script.encode(), cwd=root, capture_output=True, env=environment)
                self.assertEqual(result.returncode, 0, result.stderr)
                for name in original:
                    self.assertEqual((root / name).read_text(), 'original\n')
                self.assertTrue(trusted.resolve().is_relative_to(root.resolve()))
                shutil.rmtree(trusted)
                self.assertEqual(git('status', '--porcelain').stdout, b'')

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
                trusted = Path(directory)/'renderer-harness'
                trusted.mkdir()
                shutil.copyfile(ROOT/'scripts/release-policy.json', trusted/'release-policy.json')
                with patch.dict(os.environ, {'RUNNER_TEMP':directory}):
                    exec(compile(inline(workflow, marker), marker, "exec"), {})
                return git.call_args

    def test_reuse_requires_matching_completed_build_and_successful_packaging(self):
        run = {"head_sha": SHA, "status": "completed", "path": ".github/workflows/build-windows-release.yml", "display_title":f"Build v1.9.2 at {SHA}"}
        jobs = {"jobs": [{"steps": [{"name": "Build artifacts and verify Windows startup", "conclusion": "success"}]}]}
        self.execute("build-windows-release.yml", "PY_RUN", [run, jobs], [SHA, '1.9.2'])
        self.execute("build-windows-release.yml", "PY_RUN", [{**run,"head_sha":"b"*40},jobs],[SHA,'1.9.2'])
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute("build-windows-release.yml", "PY_RUN", [{**run,"head_sha":"b"*40},jobs],[SHA,'1.9.2'],ancestor=False)
        for field, value in [("display_title", "wrong source"), ("status", "in_progress"), ("path", "other.yml")]:
            with self.assertRaises(AssertionError):
                self.execute("build-windows-release.yml", "PY_RUN", [{**run, field: value}, jobs], [SHA,'1.9.2'])
        jobs["jobs"][0]["steps"][0]["conclusion"] = "failure"
        with self.assertRaises(AssertionError):
            self.execute("build-windows-release.yml", "PY_RUN", [run, jobs], [SHA,'1.9.2'])

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
                 "Prepare frozen validation plan", "Verify previous stable release upgrade",
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

    def test_migration_retry_reuses_full_lifecycle_but_reruns_changed_fixture(self):
        run = {"status": "completed", "conclusion": "failure", "head_sha": "b" * 40,
               "path": ".github/workflows/build-windows-release.yml", "display_title": f"Build v1.9.2 at {SHA}"}
        names = ["Verify committed versions", "Require successful core CI for the release commit",
                 "Prepare frozen validation plan", "Verify previous stable release upgrade",
                 "Restore packages from a completed artifact build", "Use current test harness for reused artifacts",
                 "Fresh installed Python 3.13 runtime and OCR", "Verify installed startup and upgrade",
                 "Verify real downloads and progress with the release controller"]
        steps = [{"name": name, "conclusion": "success"} for name in names]
        migration = {"name": "Verify v1.8.2 migration and locked dependency reuse", "conclusion": "failure"}
        steps += [migration, {"name": "Verify and collect release artifacts", "conclusion": "skipped"}]
        jobs = {"jobs": [{"name": "build", "steps": steps}]}
        args = [SHA, "1.9.2", "c" * 40, "true"]
        call = self.execute("build-windows-release.yml", "PY_VALIDATION", [run, jobs], args)
        self.assertNotIn("scripts/test_windows_previous_release.ps1", call.args[0])
        self.assertIn("scripts/test_windows_lifecycle.ps1", call.args[0])
        self.assertIn("scripts/test_windows_process_helpers.ps1", call.args[0])
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute("build-windows-release.yml", "PY_VALIDATION", [run, jobs], args, harness_changed=True)
        migration["conclusion"] = "skipped"
        with self.assertRaises(AssertionError):
            self.execute("build-windows-release.yml", "PY_VALIDATION", [run, jobs], args)
        migration["conclusion"] = "failure"
        with self.assertRaises(AssertionError):
            self.execute("build-windows-release.yml", "PY_VALIDATION", [run, jobs], args[:-1])
        steps[5]["conclusion"] = "failure"
        with self.assertRaises(AssertionError):
            self.execute("build-windows-release.yml", "PY_VALIDATION", [run, jobs], args)


if __name__ == "__main__":
    unittest.main()
