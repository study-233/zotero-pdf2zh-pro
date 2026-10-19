"""Seed/read synthetic persistent data using the installed baseline/candidate modules."""
import argparse
import importlib.util
import json
from pathlib import Path

TASK_ID = 'release-compatibility-task'
SOURCE = 'Compatibility checks preserve existing translations.'
TARGET = '兼容检查保留已有译文。'


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
    (root / 'compatibility-fixture.json').write_text(json.dumps({'taskSchema': 1, 'memory': memory}), encoding='utf-8')


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
