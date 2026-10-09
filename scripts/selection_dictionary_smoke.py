"""Opt-in live dictionary smoke; --record refreshes sanitized public-word fixtures."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

parser = argparse.ArgumentParser()
parser.add_argument('--record', action='store_true')
args = parser.parse_args()
fixtures = Path(__file__).resolve().parents[1] / 'plugin/tests/fixtures/selection-dictionaries'
for word in ('apple', 'learning', 'machine learning', 'zzzxxyynotaword'):
    body = urlencode({'q': word, 'le': 'en', 't': '3', 'client': 'web', 'keyfrom': 'webdict'}).encode()
    request = Request('https://dict.youdao.com/jsonapi_s?doctype=json&jsonversion=4', body,
                      headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'Mozilla/5.0'})
    with urlopen(request, timeout=15) as response:
        data = json.load(response)
    # Retain only the fields actually used by the adapter, no session IDs or tracking URLs.
    result = {key: data[key] for key in ('input', 'query', 'le', 'errorCode', 'code') if key in data}
    entry = data.get('ec', {}).get('word')
    if entry:
        result['ec'] = {'word': {key: entry[key] for key in ('usphone', 'ukphone', 'phone', 'return-phrase', 'prototype', 'trs', 'wfs') if key in entry}}
    pairs = data.get('blng_sents_part', {}).get('sentence-pair', [])[:3]
    if pairs:
        result['blng_sents_part'] = {'sentence-pair': [{key: pair[key] for key in ('sentence', 'sentence-eng', 'sentence-translation') if key in pair} for pair in pairs]}
    if not entry:
        for key in ('suggest', 'simple', 'web_trans'):
            if key in data:
                result[key] = {}
    print(f'youdao {word}: ec={bool(entry)}, examples={len(pairs)}, keys={list(data)}')
    if args.record:
        fixtures.mkdir(parents=True, exist_ok=True)
        (fixtures / ('youdao-' + word.replace(' ', '-') + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
try:
    request = Request('https://www.bing.com/dict/search?q=learning&FORM=BDVSP6&cc=cn', headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(request, timeout=15) as response:
        html = response.read().decode('utf-8')
    print('bing learning: dictionary markers=', 'qdef' in html, 'captcha=', 'b_captcha' in html)
    if args.record:
        # Live HTML is a local diagnostic artifact, never loaded as executable content.
        target = Path(__file__).resolve().parents[1] / '.local-dev/selection-ui/bing-learning.html'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding='utf-8')
except Exception as error:
    print('bing smoke unavailable:', type(error).__name__)
