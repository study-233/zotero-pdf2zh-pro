"""Seed/read synthetic persistent data using the installed baseline/candidate modules."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

TASK_ID = 'release-compatibility-task'
SOURCE = 'Compatibility checks preserve existing translations.'
TARGET = '兼容检查保留已有译文。'


def seed_pack(root):
    if importlib.util.find_spec('glossary_manager') is None:
        return None
    sources = [{'name': 'Synthetic upgrade fixture', 'url': 'https://example.org/terms',
                'license': 'CC0-1.0', 'licenseUrl': 'https://creativecommons.org/publicdomain/zero/1.0/'}]
    pack = {'schemaVersion': 1, 'id': 'computing', 'version': 'compatibility.1',
            'sourceLang': 'en', 'targetLang': 'zh-CN', 'sources': sources,
            'entries': [{'source': 'compatibility', 'target': '兼容性', 'tgt_lng': 'zh-CN'}]}
    content = json.dumps(pack, ensure_ascii=False).encode('utf-8')
    metadata = {key: value for key, value in pack.items() if key not in {'entries', 'schemaVersion'}}
    metadata.update(name='Upgrade fixture', entryCount=1, sizeBytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(), installedAt='2026-10-24T00:00:00+00:00',
                    url=f'https://raw.githubusercontent.com/study-233/zotero-pdf2zh-pro/{"a" * 40}/computing.json')
    folder = root / 'glossaries' / metadata['id'] / metadata['version']
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f'{metadata["sha256"]}.json').write_bytes(content)
    (folder / f'{metadata["sha256"]}.meta.json').write_text(json.dumps(metadata), encoding='utf-8')
    return metadata


def seed(root):
    from task_manager import TaskManager, TaskRecord
    root.mkdir(parents=True, exist_ok=True)
    record = TaskRecord(task_id=TASK_ID, file_name='已有论文.pdf', service='openai', output_modes=['dual'],
                        request_payload={}, workspace_dir=root / TASK_ID, status='failed')
    (root / 'tasks.json').write_text(json.dumps({'schemaVersion': 1,
        'tasks': [TaskManager._record_to_persistence(record)], 'submissions': {}}, ensure_ascii=False), encoding='utf-8')
    glossary = root / 'glossaries/compatibility.csv'
    glossary.parent.mkdir(parents=True, exist_ok=True)
    glossary.write_text('source,target\ncompatibility,兼容性\n', encoding='utf-8')
    memory = importlib.util.find_spec('translation_memory') is not None
    if memory:
        from translation_memory import TranslationMemory
        TranslationMemory(root / 'translation-memory.sqlite3').upsert('compatibility-document', [
            {'source': SOURCE, 'translation': TARGET, 'status': 'succeeded', 'page': 1, 'paragraphId': 'one'}])
    pack = seed_pack(root)
    (root / 'compatibility-fixture.json').write_text(json.dumps(
        {'taskSchema': 1, 'memory': memory, 'glossaryPack': pack}), encoding='utf-8')


def verify(root):
    from task_manager import TaskManager
    manager = TaskManager(root / 'tasks.json')
    try:
        record = manager.get_task(TASK_ID)
        if record is None or record['fileName'] != '已有论文.pdf' or record['status'] != 'failed':
            raise ValueError('Existing task was lost or changed')
    finally:
        manager.close()
    if 'compatibility,兼容性' not in (root / 'glossaries/compatibility.csv').read_text(encoding='utf-8'):
        raise ValueError('Existing glossary was lost')
    contract = json.loads((root / 'compatibility-fixture.json').read_text(encoding='utf-8'))
    if contract.get('glossaryPack'):
        from glossary_manager import GlossaryManager
        metadata = contract['glossaryPack']
        glossary_manager = GlossaryManager(root / 'glossaries', catalog={'schemaVersion': 1, 'packs': [metadata]})
        try:
            if glossary_manager.list_packs()[0]['status'] != 'installed':
                raise ValueError('Downloaded glossary is no longer recognized after upgrade')
            reference = {key: metadata[key] for key in ('id', 'version', 'sha256')}
            terms, _ = glossary_manager.task_snapshot([reference], [], 'en', 'zh-CN')
            if terms != [{'source': 'compatibility', 'target': '兼容性', 'tgt_lng': 'zh-cn'}]:
                raise ValueError('Downloaded glossary cannot be used after upgrade')
        finally:
            glossary_manager.close()
    if contract['memory']:
        from translation_memory import TranslationMemory
        if TranslationMemory(root / 'translation-memory.sqlite3').lookup('compatibility-document', SOURCE)['translation'] != TARGET:
            raise ValueError('Existing translation memory was lost')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('seed', 'verify'))
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    (seed if args.action == 'seed' else verify)(args.root)
    print(f'{args.action}: persistent task, glossary and available memory format passed')
