"""Exercise the release entrypoint's actual local/remote validation decision."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

SOURCE = Path(__file__).with_name('release.sh').read_text(encoding='utf-8')
ROUTING = SOURCE.split('# BEGIN_VALIDATION_ROUTING', 1)[1].split('# END_VALIDATION_ROUTING', 1)[0]
GIT_BASH = Path(shutil.which('git') or 'git').parent.parent / 'bin/bash.exe'
BASH = str(GIT_BASH) if GIT_BASH.is_file() else shutil.which('bash')


@unittest.skipUnless(BASH, 'Bash is required for the release coordinator')
class ValidationRoutingTests(unittest.TestCase):
    def route(self, platform, full=True, force_remote=False, replacement='', extended=None):
        environment = dict(os.environ, OS=platform, FULL_VALIDATION=str(full).lower(),
                           EXTENDED_VALIDATION=str(full if extended is None else extended).lower(),
                           REMOTE_VALIDATION=str(force_remote).lower(), REPLACE_EXISTING=replacement)
        result = subprocess.run([BASH, '-c', ROUTING +
                                 '\nprintf "%s %s" "$REMOTE_FULL_VALIDATION" "$LOCAL_EXTENDED_VALIDATION"'],
                                env=environment, capture_output=True, text=True, check=True)
        return result.stdout

    def test_windows_prefers_local_extended_validation(self):
        self.assertEqual(self.route('Windows_NT'), 'true true')

    def test_data_changes_defer_only_historical_upgrades_without_forcing_full_lifecycle(self):
        self.assertEqual(self.route('Windows_NT', full=False, extended=True), 'false true')
        self.assertEqual(self.route('Darwin', full=False, extended=True), 'false false')

    def test_other_hosts_and_windows_fallback_use_remote_validation(self):
        self.assertEqual(self.route('Darwin'), 'true false')
        self.assertEqual(self.route('Windows_NT', force_remote=True), 'true false')

    def test_standard_releases_do_not_gain_unrequested_extended_tests(self):
        for platform in ('Windows_NT', 'Darwin'):
            self.assertEqual(self.route(platform, full=False), 'false false')

    def test_replacement_keeps_the_required_remote_full_validation(self):
        self.assertEqual(self.route('Windows_NT', replacement='a' * 40), 'true false')

    def test_local_upgrade_invocation_skips_both_completed_standard_phases(self):
        arguments=next(line.strip() for line in SOURCE.splitlines() if line.strip().startswith('extended_args=('))
        result=subprocess.run([BASH,'-c',arguments+'\nprintf "%s\\n" "${extended_args[@]}"'],
                              env=dict(os.environ,VERSION='1.9.3',VALIDATION_PLAN='plan.json',VALIDATION_RECEIPT='receipt.json'),
                              capture_output=True,text=True,check=True)
        self.assertIn('-SkipLifecycle',result.stdout.splitlines())
        self.assertIn('-SkipRecentUpgrade',result.stdout.splitlines())


if __name__ == '__main__':
    unittest.main()
