"""Three paired PDF runs with frozen clients and shared warm-cache seeds."""
import argparse
from pathlib import Path
import shutil
import time

from .common import code_identity, file_hash, read, rows, write
from .corpus import verify
from .product import run_pdf
from .providers import validate_config


def run(data, config, before, after, output, *, execute=False, resume=False, max_requests=180, max_seconds=5400):
    validate_config(config)
    if config['provider'] != 'chatgpt' or max_requests < 1 or max_seconds < 1:
        raise ValueError('ChatGPT config and positive shared limits required')
    if not execute:
        return {'rounds': 3, 'pages_per_run': 4, 'cache_modes': ['cold', 'warm'],
                'max_requests': max_requests, 'max_seconds': max_seconds, 'executes_model': False}
    output = Path(output)
    if resume:
        identity = read(output / 'comparison.json')
        if (identity['config'] != config or identity['data'] != verify(data)['content_sha256']
                or identity['limits'] != {'max_requests':max_requests,'max_seconds':max_seconds}):
            raise ValueError('Comparison resume configuration drift')
        for name, source in [('before', before), ('after', after)]:
            if file_hash(source) != identity[name]['sha256'] or file_hash(Path(identity[name]['file'])) != identity[name]['sha256']:
                raise ValueError('Comparison resume client drift')
        completed = {r['directory'] for r in identity['runs']}
        for path in output.glob('round-*'):
            if path.name not in completed or read(path/'run.json')['status'] != 'completed':
                raise ValueError('Incomplete or uncertain run; reconcile it before continuing')
        identity['resumed_code'] = code_identity()
    else:
        output.mkdir(parents=True, exist_ok=False)
        identity = {'code': code_identity(), 'data': verify(data)['content_sha256'],
                    'config': config, 'limits': {'max_requests': max_requests, 'max_seconds': max_seconds},
                    'baseline_promoted': False, 'status': 'running', 'runs': []}
        for name, source in [('before', before), ('after', after)]:
            destination = output / f'{name}_client.py'
            shutil.copy2(source, destination)
            identity[name] = {'file': str(destination.resolve()), 'sha256': file_hash(destination)}
    identity['status'] = 'running'
    write(output / 'comparison.json', identity)
    started, used = time.monotonic(), identity.get('used_requests', 0)
    previous_seconds = identity.get('elapsed_seconds', 0)
    completed = {r['directory'] for r in identity['runs']}
    try:
        for index in range(1, 4):
            order = ('before', 'after') if index % 2 else ('after', 'before')
            for cache in ('cold', 'warm'):
                for variant in order:
                    target = output / f'round-{index}-{variant}-{cache}'
                    if target.name in completed:
                        continue
                    seconds_left = int(max_seconds - previous_seconds - (time.monotonic() - started))
                    if seconds_left <= 0 or used >= max_requests:
                        raise ValueError('Shared comparison budget exhausted')
                    frozen = identity[variant]
                    current = {**config, 'experiment_transport': 'chatgpt_connection_probe',
                               'client_file': frozen['file'], 'client_sha256': frozen['sha256'], 'cache': cache,
                               'limits': {'max_requests': max_requests - used, 'max_seconds': seconds_left}}
                    if cache == 'warm':
                        # Identical seed contents for both variants in each pair.
                        current['cache_seed'] = str((output / f'round-{index}-before-cold').resolve())
                    print(f'START {target.name}', flush=True)
                    try:
                        result = run_pdf(data, current, 'pdf-quick', target)
                    finally:
                        events = [e for p in (target / 'runtime' / 'ledger').glob('*.jsonl') for e in rows(p)]
                        calls = len({e['callId'] for e in events if e['event'] == 'request_started'})
                        used += calls
                    results = rows(target / 'results.jsonl')
                    identity['runs'].append({'round': index, 'variant': variant, 'cache': cache,
                                             'directory': target.name, 'calls': calls,
                                             'seconds': sum(r['elapsed_seconds'] for r in results),
                                             'ok': sum(r['status'] == 'ok' for r in results)})
                    identity['used_requests'] = used
                    write(output / 'comparison.json', identity)
                    print(f'END {target.name}: {calls} calls, {identity["runs"][-1]["seconds"]:.2f}s', flush=True)
                    if result['status'] != 'completed' or len(results) != 4 or any(r['status'] != 'ok' for r in results):
                        raise ValueError('Incomplete PDF run; retained evidence, no automatic resubmission')
        identity['status'] = 'completed'
    except BaseException as error:
        identity.update(status='stopped', error_type=type(error).__name__)
        raise
    finally:
        identity.update(used_requests=used, elapsed_seconds=previous_seconds + time.monotonic() - started)
        write(output / 'comparison.json', identity)
    return identity


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('data', 'config', 'before', 'after', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--max-requests', type=int, default=180)
    p.add_argument('--max-seconds', type=int, default=5400)
    args = p.parse_args()
    result = run(args.data, read(args.config), args.before, args.after, args.output,
                 execute=args.execute, resume=args.resume, max_requests=args.max_requests, max_seconds=args.max_seconds)
    print(result['status'] if 'status' in result else result)
