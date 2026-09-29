"""Freeze the public Command Code pricing page, including tiers and peak rates."""
import json
import re
from bench import client, checked, read, write, now, digest, DEFAULT_WORK


def extract(html):
    match = re.search(r'streamController.enqueue\(("(?:[^"\\]|\\.)*")\)', html)
    if not match:
        raise ValueError('Pricing page format changed; verify manually, never guess prices')
    flat = json.loads(json.loads(match.group(1)))
    def hydrate(index):
        if index < 0:
            return None
        item = flat[index]
        if isinstance(item, dict):
            return {flat[int(k[1:])]: hydrate(v) for k, v in item.items()}
        if isinstance(item, list):
            return [hydrate(v) for v in item]
        return item
    name = '_' + str(flat.index('name'))
    cost = '_' + str(flat.index('inputCost'))
    return [hydrate(i) for i, entry in enumerate(flat) if isinstance(entry, dict) and name in entry and cost in entry]


if __name__ == '__main__':
    snapshot = read(DEFAULT_WORK / 'snapshot.json')
    with client() as c:
        response = checked(c.get('https://commandcode.ai/models'))
    html = response.content.decode('utf-8')
    (DEFAULT_WORK / 'pricing.html').write_text(html, encoding='utf-8')
    models = extract(html)
    selected = []
    for model in snapshot['models']:
        matches = [m for m in models if m['name'] == model['name']]
        if len(matches) != 1:
            raise ValueError('Ambiguous pricing model: ' + model['name'])
        entry = matches[0]
        selected.append(entry)
        for local, remote in [('input', 'inputCost'), ('output', 'outputCost'), ('cacheRead', 'cacheReadCost')]:
            model[local] = entry[remote]
        model['priceDetails'] = entry
    snapshot.update(pricingVerified=True, pricingSource='https://commandcode.ai/models',
                    pricingCheckedAt=now(), pricingSha256=digest(DEFAULT_WORK / 'pricing.html'))
    write(DEFAULT_WORK / 'verified-prices.json', selected)
    write(DEFAULT_WORK / 'snapshot.json', snapshot)
    print(json.dumps(selected, ensure_ascii=True, indent=2))
