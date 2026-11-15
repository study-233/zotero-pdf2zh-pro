import base64
import json
import unittest
from unittest.mock import patch

import release_docs as docs


class PublicDocsTests(unittest.TestCase):
    def release(self):
        return {'tag_name': 'v1.9.8', 'draft': False, 'prerelease': False,
                'assets': [{'name': name, 'state': 'uploaded', 'size': 123} for name in docs.CLIENTS]}

    def test_promotion_waits_for_both_public_installers(self):
        for change in ({'draft': True}, {'prerelease': True}, {'tag_name': 'v1.9.7'},
                       {'assets': self.release()['assets'][:1]}):
            with self.subTest(change=change), patch.object(docs, 'api', return_value=self.release() | change), \
                    patch.object(docs, 'promote_pr') as merge:
                with self.assertRaises(ValueError):
                    docs.publish('1.9.8', 10)
                merge.assert_not_called()

    def test_idempotent_marker_update_and_main_blob_guard(self):
        old = 'stable\n<!-- release-version --> `1.9.7`'
        new = old.replace('1.9.7', '1.9.8')
        with patch.object(docs, 'check_release'), patch.object(docs, 'readme', side_effect=[
                (old, 'original'), (new, 'updated'), (new, 'updated')]), \
                patch.object(docs.subprocess, 'run') as update:
            docs.publish('1.9.8')
            docs.publish('1.9.8')
            update.assert_called_once()
            payload = json.loads(update.call_args.kwargs['input'])
            self.assertEqual((payload['sha'], payload['branch']), ('original', 'main'))
            self.assertEqual(base64.b64decode(payload['content']).decode(), new)

    def test_documentation_pr_is_bound_to_reviewed_head_and_files(self):
        pr = {'base': {'repo': {'full_name': docs.REPO}, 'ref': 'main'},
              'head': {'repo': {'full_name': docs.REPO}, 'sha': 'reviewed'},
              'merged': False, 'draft': False, 'state': 'open', 'changed_files': 1}
        for file in ({'filename': 'README.md'}, {'filename': 'assets/x', 'previous_filename': 'server/app.py'}):
            commands = [json.dumps([[file]]), b'']
            with patch.object(docs, 'api', side_effect=[pr, {'merged': True}]), \
                    patch.object(docs, 'command', side_effect=commands) as run, \
                    patch.object(docs, 'check_release'), \
                    patch.object(docs, 'readme', return_value=('<!-- release-version --> `1.9.8`', 'blob')):
                if 'previous_filename' in file:
                    with self.assertRaisesRegex(ValueError, 'Unexpected file'):
                        docs.promote_pr(10, '1.9.8')
                    self.assertEqual(run.call_count, 1)
                else:
                    docs.promote_pr(10, '1.9.8')
                    self.assertEqual(run.call_args.args[0][-2:], ['--match-head-commit', 'reviewed'])
