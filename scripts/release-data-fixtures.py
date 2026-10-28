"""Seed/read synthetic persistent data using the installed baseline/candidate modules."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

TASK_ID = 'release-compatibility-task'
SOURCE = 'Compatibility checks preserve existing translations.'
TARGET = '兼容检查保留已有译文。'


def cache_module():
    name = 'pdf2zh_pro' if importlib.util.find_spec('pdf2zh_pro') else 'pdf2zh_next'
    return __import__(name + '.translator.cache', fromlist=['TranslationCache'])


def recovery_document():
    from babeldoc.format.pdf.document_il import Document, Page, PdfParagraph, PdfParagraphComposition, PdfCharacter
    paragraph = PdfParagraph(debug_id='compatibility-one', unicode=SOURCE,
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=PdfCharacter(char_unicode='C'))])
    return Document(page=[Page(page_number=0, pdf_paragraph=[paragraph])]), paragraph


def seed_extended(root):
    """Use the baseline's writers, including the old core's actual cache keys."""
    from selection_cache import SelectionCache
    from babeldoc.format.pdf.translation_recovery import TranslationRecovery
    from chatgpt_auth import private_write
    cache_module().TranslationCache('openai', {'model': 'fixture', 'lang_out': 'zh-CN'}).set(SOURCE, TARGET)
    selection = SelectionCache(root / 'selection-cache.sqlite3')
    selection.put('fixture-translation', 'translation', {'translation': TARGET})
    selection.put('fixture-dictionary', 'dictionary', {'headword': 'compatibility', 'senses': [{'chinese': '兼容性'}]})
    path = root / TASK_ID / 'paragraph-recovery.sqlite3'
    document, paragraph = recovery_document()
    recovery = TranslationRecovery(path, 'compatibility-parser-v1')
    try:
        recovery.register(document, SimpleNamespace(skip_references=False, min_text_length=3))
        recovery.attempt([paragraph], 'synthetic recovery context')
        recovery.record(paragraph, 'succeeded', input=SOURCE, translation=TARGET)
        recovery.record_quality(paragraph, 'passed', 1)
    finally:
        recovery.close()
    private_write(root / 'chatgpt-auth/credentials.json', {
        'issuer': 'https://auth.openai.com', 'subject': 'synthetic-fixture', 'client_id': 'synthetic-client',
        'access_token': 'synthetic-not-a-real-token', 'refresh_token': 'synthetic-not-a-real-refresh-token',
        'expires_at': 4102444800, 'scopes': ['chatgpt.tokens.use.direct']})
    # Outputs and diagnostics must survive byte-for-byte, independently of database migrations.
    files = {'existing-output.pdf': b'%PDF-1.4\n% synthetic retained output\n%%EOF\n',
             'diagnostics.json': b'{"schemaVersion":1,"events":[]}',
             'paragraph-recovery.json': b'{"version":1,"fingerprint":"old-backup","paragraphs":{}}'}
    hashes = {}
    for name, data in files.items():
        relative = Path(TASK_ID) / name
        (root / relative).write_bytes(data)
        hashes[relative.as_posix()] = hashlib.sha256(data).hexdigest()
    relative = 'chatgpt-auth/credentials.json'
    hashes[relative] = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    return hashes


def verify_extended(root, hashes):
    from selection_cache import SelectionCache
    from babeldoc.format.pdf.translation_recovery import TranslationRecovery
    from chatgpt_auth import AuthStore
    if cache_module().TranslationCache('openai', {'model': 'fixture', 'lang_out': 'zh-CN'}).get(SOURCE) != TARGET:
        raise ValueError('Historical model cache no longer hits after upgrade')
    selection = SelectionCache(root / 'selection-cache.sqlite3')
    if selection.get('fixture-translation') != {'translation': TARGET}:
        raise ValueError('Existing selection translation was lost')
    if selection.get('fixture-dictionary')['senses'] != [{'chinese': '兼容性'}]:
        raise ValueError('Existing dictionary entry was lost')
    document, paragraph = recovery_document()
    recovery = TranslationRecovery(root / TASK_ID / 'paragraph-recovery.sqlite3', 'compatibility-parser-v1')
    try:
        recovery.register(document, SimpleNamespace(skip_references=False, min_text_length=3))
        if recovery.restored(paragraph, SOURCE) != TARGET:
            raise ValueError('Existing paragraph cannot be resumed')
        if recovery.connection.execute('SELECT prompt FROM contexts').fetchone()[0] != 'synthetic recovery context':
            raise ValueError('Existing recovery context was lost')
        if not any(entry.get('quality', {}).get('status') == 'passed' for entry in recovery.entries.values()):
            raise ValueError('Existing quality review was lost')
    finally:
        recovery.close()
    store = AuthStore(root / 'chatgpt-auth', client=object())
    if not store.status()['authenticated']:
        raise ValueError('Existing synthetic account is no longer recognized')
    for relative, digest in hashes.items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != digest:
            raise ValueError(f'Preserved data changed: {relative}')


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
    from task_manager import TaskManager, TaskRecord, TranslationOutputFile
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / TASK_ID
    workspace.mkdir(exist_ok=True)
    (workspace / 'input.pdf').write_bytes(b'%PDF-1.4\n% retained synthetic source\n%%EOF\n')
    (workspace / 'existing-output.pdf').write_bytes(b'%PDF-1.4\n% synthetic retained output\n%%EOF\n')
    record = TaskRecord(task_id=TASK_ID, file_name='已有论文.pdf', service='openai', output_modes=['dual'],
                        request_payload={'input_path': str(workspace / 'input.pdf'), 'output_dir': str(workspace),
                                         'translation_memory_path': str(root / 'translation-memory.sqlite3')},
                        workspace_dir=workspace, status='failed', result_files={'dual': TranslationOutputFile(
                            output_mode='dual', output_path=workspace / 'existing-output.pdf', filename='已有译文.pdf')})
    (root / 'tasks.json').write_text(json.dumps({'schemaVersion': 1,
        'tasks': [TaskManager._record_to_persistence(record)], 'submissions': {}}, ensure_ascii=False), encoding='utf-8')
    (root / 'tasks.json.bak').write_bytes((root / 'tasks.json').read_bytes())
    glossary = root / 'glossaries/compatibility.csv'
    glossary.parent.mkdir(parents=True, exist_ok=True)
    glossary.write_text('source,target\ncompatibility,兼容性\n', encoding='utf-8')
    memory = importlib.util.find_spec('translation_memory') is not None
    if memory:
        from translation_memory import TranslationMemory
        TranslationMemory(root / 'translation-memory.sqlite3').upsert('compatibility-document', [
            {'source': SOURCE, 'translation': TARGET, 'status': 'succeeded', 'page': 1, 'paragraphId': 'one'}])
    pack = seed_pack(root)
    # Older supported releases can lack selection/account support.
    extended = seed_extended(root) if all(importlib.util.find_spec(name) is not None
        for name in ('selection_cache', 'chatgpt_auth')) else None
    (root / 'compatibility-fixture.json').write_text(json.dumps(
        {'taskSchema': 1, 'memory': memory, 'glossaryPack': pack, 'extended': extended, 'pathChecks': True}), encoding='utf-8')


def verify(root):
    from task_manager import TaskManager
    root = root.resolve()
    contract = json.loads((root / 'compatibility-fixture.json').read_text(encoding='utf-8'))
    for name in (('tasks.json', 'tasks.json.bak') if contract.get('pathChecks') else ()):
        task = next(task for task in json.loads((root / name).read_text(encoding='utf-8'))['tasks'] if task['task_id'] == TASK_ID)
        if Path(task['workspace_dir']) != root / TASK_ID:
            raise ValueError('Task workspace still points to the old installation')
        if Path(task['request_payload']['input_path']).read_bytes() != (root / TASK_ID / 'input.pdf').read_bytes():
            raise ValueError('Existing input PDF is inaccessible')
        if Path(task['result_files']['dual']['output_path']).read_bytes() != (root / TASK_ID / 'existing-output.pdf').read_bytes():
            raise ValueError('Existing output PDF is inaccessible')
        if Path(task['request_payload']['translation_memory_path']) != root / 'translation-memory.sqlite3':
            raise ValueError('Task memory still points to the old installation')
    manager = TaskManager(root / 'tasks.json')
    try:
        record = manager.get_task(TASK_ID)
        if record is None or record['fileName'] != '已有论文.pdf' or record['status'] != 'failed':
            raise ValueError('Existing task was lost or changed')
    finally:
        manager.close()
    if 'compatibility,兼容性' not in (root / 'glossaries/compatibility.csv').read_text(encoding='utf-8'):
        raise ValueError('Existing glossary was lost')
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
    if contract.get('extended'):
        verify_extended(root, contract['extended'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('seed', 'verify'))
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    # Import-time caches/configuration must never use the developer's real profile.
    os.environ['PDF2ZH_TRANSLATION_CACHE_DIR'] = str(args.root / 'translation-cache')
    os.environ['PDF2ZH_CONFIG_DIR'] = str(args.root / 'config')
    os.environ['PDF2ZH_DATA_DIR'] = str(args.root)
    (seed if args.action == 'seed' else verify)(args.root)
    print(f'{args.action}: persistent task, glossary, memory and supported cache/recovery/account formats passed')
