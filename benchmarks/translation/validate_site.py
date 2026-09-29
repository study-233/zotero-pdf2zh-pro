"""Offline public-artifact checks. Never call a provider from CI."""
import json
from pathlib import Path
import re

site=Path(__file__).parent/'site'
data=json.loads((site/'results.json').read_text(encoding='utf-8'))
assert data['schemaVersion']==1
assert data.get('visibility')=='public', 'Never publish private review packets'
models={m['id'] for m in data['models']}
papers={p['id'] for p in data['papers']}
assert len(models)==5 and len(papers)==3
keys=set()
for row in data['results']:
    assert row['model'] in models and row['paper'] in papers
    key=(row['phase'],row['model'],row['paper'])
    assert key not in keys
    keys.add(key)
    assert row['costUsd'] is None or row['costUsd']>=0
    assert row['quality'] is None or 0<=row['quality']<=100
for path in site.rglob('*'):
    if path.is_file() and path.suffix in {'.html','.js','.css','.json','.csv'}:
        text=path.read_text(encoding='utf-8-sig')
        assert not re.search(r'(?:user_|sk-)[A-Za-z0-9_-]{24,}',text), f'Credential-like value in {path.name}'
        assert not re.search(r'Bearer\s+[A-Za-z0-9_-]{16,}',text), f'Authorization value in {path.name}'
print('Static benchmark data validated; no paid calls performed.')
