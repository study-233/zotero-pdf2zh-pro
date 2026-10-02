"""Offline public-artifact checks. Never call a provider from CI."""
import json
from pathlib import Path
import re
import hashlib

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
manifest=json.loads((site/'pdfs.json').read_text(encoding='utf-8'))
assert manifest['schemaVersion']==1 and manifest['visibility']=='public'
pdf_keys=set()
for document in manifest['documents']:
    key=(document['model'],document['paper'])
    assert key not in pdf_keys and key[0] in models and key[1] in papers
    pdf_keys.add(key)
    assert re.fullmatch(r'[a-zA-Z0-9_-]+',document['taskId'])
    if document.get('supplemental'):
        extra=document['supplementaryRun']
        assert extra['reviewStatus'] in ('not_reviewed','verified')
        assert extra['taskId']==document['taskId']
        assert any(r['taskId']==extra['supplementalTo'] and r['model']==key[0] and r['paper']==key[1]
                   for r in data.get('originalResults',data['results']))
        if extra['reviewStatus']=='verified':
            assert any(r['taskId']==extra['taskId'] and r['reviewStatus']=='verified' and
                       r['reviewSha256']==extra['reviewSha256'] for r in data['results'])
    if document.get('url'):
        assert document['url']==f"pdfs/{document['taskId']}.pdf"
        assert data.get('publishFullPdfs') is True or next(p for p in data['papers'] if p['id']==key[1])['redistributionApproved'] is True
        assert hashlib.sha256((site/document['url']).read_bytes()).hexdigest()==document['sha256']
permitted={d['url'] for d in manifest['documents'] if d.get('url')}
assert all(str(p.relative_to(site)).replace('\\','/') in permitted for p in site.rglob('*.pdf'))
for path in site.rglob('*'):
    if path.is_file() and path.suffix in {'.html','.js','.css','.json','.csv'}:
        text=path.read_text(encoding='utf-8-sig')
        assert not re.search(r'(?:user_|sk-)[A-Za-z0-9_-]{24,}',text), f'Credential-like value in {path.name}'
        assert not re.search(r'Bearer\s+[A-Za-z0-9_-]{16,}',text), f'Authorization value in {path.name}'
print('Static benchmark data validated; no paid calls performed.')
