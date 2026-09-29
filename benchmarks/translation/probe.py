"""Small paid access probe; never silently converts an access failure to a score."""
import os
import argparse
from pathlib import Path
from bench import DEFAULT_WORK, client, read, write, now

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,default=DEFAULT_WORK)
    work=parser.parse_args().work.resolve()
    snapshot = read(work / 'snapshot.json')
    key = os.environ['COMMAND_CODE_API_KEY']
    path = work / 'access.json'
    results = read(path) if path.exists() else []
    with client() as c:
        for model in snapshot['models']:
            if any(r['model'] == model['id'] for r in results):
                continue
            response = c.post(snapshot['providerBase'] + '/chat/completions',
                headers={'Authorization': 'Bearer ' + key},
                json={'model': model['id'], 'messages': [{'role': 'user', 'content': 'Reply OK.'}], 'max_tokens': 16})
            data = response.json()
            error = data.get('error') or {}
            message = str(error.get('message', ''))[:1000].replace(key, '[REDACTED]')
            result = {'model': model['id'], 'timestamp': now(), 'status': response.status_code,
                      'code': error.get('code'), 'message': message, 'usage': data.get('usage')}
            results.append(result)
            model.update(accessStatus=result['status'],accessCheckedAt=result['timestamp'])
            write(path, results)
            write(work / 'snapshot.json', snapshot)
            print(model['name'], response.status_code, message, flush=True)
