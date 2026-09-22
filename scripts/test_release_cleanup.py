"""Run the real coordinator EXIT handler without any publication commands."""
from pathlib import Path
import shutil
import subprocess
import unittest


SOURCE = Path(__file__).with_name("release.sh").read_text(encoding="utf-8")
CLEANUP = SOURCE.split("# BEGIN_RELEASE_CLEANUP\n", 1)[1].split("# END_RELEASE_CLEANUP", 1)[0]
GIT = Path(shutil.which("git") or "git")
WINDOWS_BASH = GIT.parent.parent / "bin/bash.exe"
BASH = str(WINDOWS_BASH) if WINDOWS_BASH.is_file() else shutil.which("bash")


@unittest.skipUnless(BASH, "Bash is required for the release coordinator")
class ReleaseCleanupTests(unittest.TestCase):
    def invoke(self, status=7, backup="this invocation backup", tap=0, restore_status=0):
        script = f'''set -euo pipefail
TEMP_PATHS=()
REPLACEMENT_BACKUP='{backup}'
REPLACEMENT_TAP_PUSHED={tap}
uv() {{ printf 'RESTORE %s\\n' "$*"; return {restore_status}; }}
{CLEANUP}
trap cleanup EXIT
exit {status}
'''
        # Bash and Git both need LF bytes when launched from Windows Python.
        return subprocess.run([BASH, "--noprofile", "--norc"], input=script.encode(), capture_output=True)

    def test_later_failure_restores_only_the_current_invocation_backup(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 7)
        self.assertIn(b"restore this invocation backup", result.stdout)

    def test_success_and_failures_before_publication_do_not_restore(self):
        for result in [self.invoke(status=0), self.invoke(backup="")]:
            self.assertNotIn(b"RESTORE", result.stdout)

    def test_recovery_failure_keeps_original_exit_status_and_reports_backup(self):
        result = self.invoke(restore_status=1)
        self.assertEqual(result.returncode, 7)
        self.assertIn(b"Automatic recovery stopped", result.stderr)
        self.assertIn(b"this invocation backup", result.stderr)

    def test_tap_failure_reports_tap_state_without_rewriting_its_history(self):
        result = self.invoke(tap=1)
        self.assertEqual(result.returncode, 7)
        self.assertIn(b"Homebrew tap update was attempted", result.stderr)
        self.assertEqual(result.stdout.count(b"RESTORE"), 1)


if __name__ == "__main__":
    unittest.main()
