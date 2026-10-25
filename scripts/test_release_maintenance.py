"""Rolling baselines, interruption recovery and bounded evidence persistence."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import release_control as control
import release_policy as policy
import workspace


class RollingBaselineTests(unittest.TestCase):
    def test_release_risk_and_full_lifecycle_do_not_select_old_versions(self):
        for changes in ([], ['server/runtime_storage.py'], ['server/uv.lock'], ['new/platform']):
            for full in (False, True):
                plan = policy.select(changes, '1.9.4', '1.9.5', full=full, recent=['1.9.4','1.9.3'])
                self.assertEqual(plan['cases'], [{'id':'recent-upgrade','baseline':'1.9.4'}])
                if full or changes == ['new/platform']: self.assertEqual(plan['lifecycle'], 'extended')

    def test_periodic_window_rolls_forward_and_explicit_legacy_is_separate(self):
        plan = policy.select([], '1.9.3','1.9.4',compatibility=True,recent=['1.9.3','1.9.2'])
        self.assertEqual([c['baseline'] for c in plan['cases']],['1.9.3','1.9.2'])
        plan = policy.select([], '1.9.4','1.9.5',explicit=['v1.8.2','1.9.4','1.8.2'])
        self.assertEqual(plan['cases'],[{'id':'recent-upgrade','baseline':'1.9.4'}, {'id':'legacy-upgrade','baseline':'1.8.2'}])
        with self.assertRaises(ValueError): policy.select([], '1.9.4','1.9.5',explicit=['1.9.5'])
        with self.assertRaises(ValueError): policy.select([], '1.9.4','1.9.5',recent=['1.9.4','1.9.3','1.8.2'])


class ReceiptTests(unittest.TestCase):
    def test_only_exact_passed_case_can_be_reused_after_interruption(self):
        plan=policy.select([], '1.9.3','1.9.4')
        plan.update(sourceCommit='a'*40,harnessSha256='b'*64,baselines={'1.9.3':{
            'package':{'sha256':'c'*64},'wheel':{'sha256':'d'*64}}})
        artifacts={control.PACKAGE:{'sha256':'e'*64},'zotero_pdf2zh_pro-1.9.4-py3-none-any.whl':{'sha256':'f'*64}}
        receipt={'status':'passed','version':'1.9.4','previousVersion':'1.9.3','commit':'a'*40,
                 'planSha256':control.plan_digest(plan),'harnessSha256':'b'*64,
                 'packageSha256':'e'*64,'wheelSha256':'f'*64,'previousPackageSha256':'c'*64,'previousWheelSha256':'d'*64}
        control.verify_case_receipt(plan,'recent-upgrade','1.9.3',receipt,artifacts)
        for field in receipt:
            with self.subTest(field=field), self.assertRaises(ValueError):
                control.verify_case_receipt(plan,'recent-upgrade','1.9.3',dict(receipt,**{field:'changed'}),artifacts)


class ArtifactCacheTests(unittest.TestCase):
    def test_same_immutable_archive_is_reused_and_corruption_redownloads(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive: archive.writestr('checksums.json','{}')
        payload=stream.getvalue()
        artifact={'id':7,'name':'release-fixture','expired':False,'digest':'sha256:'+hashlib.sha256(payload).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def download(args,destination): Path(destination).write_bytes(payload)
            with patch.object(control,'ROOT',root),patch.object(control,'api',return_value={'artifacts':[artifact]}), \
                 patch.object(control,'download',side_effect=download) as transfer:
                control.download_artifact(123,'release-fixture',root/'first')
                control.download_artifact(123,'release-fixture',root/'second')
                self.assertEqual(transfer.call_count,1)
                (root/'.tmp/cache/release-artifacts/7.zip').write_bytes(b'corrupt')
                control.download_artifact(123,'release-fixture',root/'third')
                self.assertEqual(transfer.call_count,2)
                self.assertEqual((root/'third/checksums.json').read_text(),'{}')


class PersistenceTests(unittest.TestCase):
    def test_transient_windows_sharing_error_retries_but_other_errors_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'state.json'
            error=PermissionError('sharing'); error.winerror=32
            original=Path.replace
            calls=[]
            def replace(path,destination):
                calls.append(path)
                if len(calls)==1: raise error
                return original(path,destination)
            with patch.object(Path,'replace',replace),patch.object(workspace.time,'sleep'):
                workspace.save(target,{'status':'passed'})
            self.assertEqual(json.loads(target.read_text()),{'status':'passed'})
            self.assertEqual(len(calls),2)
            with patch.object(Path,'replace',side_effect=PermissionError('permanent')) as replace,patch.object(workspace.time,'sleep'):
                with self.assertRaises(PermissionError): workspace.save(target,{})
                self.assertEqual(replace.call_count,1)
            with patch.object(Path,'replace',side_effect=error) as replace,patch.object(workspace.time,'sleep'):
                with self.assertRaises(PermissionError): workspace.save(target,{})
                self.assertEqual(replace.call_count,5)
            self.assertEqual(json.loads(target.read_text()),{'status':'passed'})

    def test_archive_prunes_dependencies_and_retains_pending_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=workspace.Workspace(Path(directory))
            run=manager.start('fixture'); work=Path(run['work'])
            (work/'runtime').mkdir();(work/'runtime/large.log').write_text('dependency')
            (work/'result.log').write_text('evidence')
            with patch.object(workspace,'remove',side_effect=PermissionError('locked')):
                report=Path(manager.finish(work))
            with zipfile.ZipFile(report/'evidence.zip') as archive:
                self.assertEqual(archive.namelist(),['result.log'])
            self.assertEqual(json.loads((report/'.workspace.json').read_text())['cleanupStatus'],'pending')
            self.assertTrue(work.exists())


if __name__ == '__main__':
    unittest.main()
