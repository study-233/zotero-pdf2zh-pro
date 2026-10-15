"""Bounded, cold-cache PDF comparison of two and four ChatGPT workers."""
import argparse
from pathlib import Path
import shutil
import time

from .common import ROOT, code_identity, file_hash, read, rows, write
from .corpus import verify
from .product import run_pdf
from .providers import validate_config


def run(data, config, output, *, execute=False, max_requests=120, max_seconds=1800):
    validate_config(config)
    if config['provider'] != 'chatgpt' or config.get('cache', 'cold') != 'cold':
        raise ValueError('This experiment requires cold-cache ChatGPT')
    if max_requests < 1 or max_seconds < 1:
        raise ValueError('Positive shared limits required')
    if not execute:
        return {'rounds': 3, 'order': [2, 4, 4, 2, 2, 4], 'qps': 2,
                'max_requests': max_requests, 'max_seconds': max_seconds, 'executes_model': False}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    client = output / 'client.py'
    shutil.copy2(ROOT / 'server/chatgpt_client.py', client)
    identity = {'status': 'running', 'code': code_identity(), 'data': verify(data)['content_sha256'],
                'config': config, 'client_sha256': file_hash(client), 'baseline_promoted': False,
                'limits': {'max_requests': max_requests, 'max_seconds': max_seconds},
                'criterion': {'all_pairs_faster': True, 'median_reduction_percent': 15,
                              'no_extra_failures_or_calls': True}, 'runs': []}
    write(output / 'comparison.json', identity)
    started, used = time.monotonic(), 0
    try:
        for index in range(1, 4):
            for workers in ((2, 4) if index % 2 else (4, 2)):
                # A changing product would invalidate subsequent paired runs.
                if code_identity() != identity['code']:
                    raise ValueError('Source changed during the comparison')
                remaining = int(max_seconds - (time.monotonic() - started))
                if remaining <= 0 or used >= max_requests:
                    raise ValueError('Shared comparison budget exhausted')
                target = output / f'round-{index}-workers-{workers}'
                current = {**config, 'cache': 'cold', 'experiment_transport': 'chatgpt_connection_probe',
                           'experimental_worker_cap': 4, 'client_file': str(client.resolve()),
                           'client_sha256': identity['client_sha256'],
                           'pdf_options': {**config.get('pdf_options', {}), 'qps': 2, 'poolSize': workers},
                           'limits': {'max_requests': max_requests - used, 'max_seconds': remaining}}
                print(f'START {target.name}', flush=True)
                try:
                    result = run_pdf(data, current, 'pdf-quick', target)
                finally:
                    events = [e for p in (target / 'runtime/ledger').glob('*.jsonl') for e in rows(p)]
                    calls = len({e['callId'] for e in events if e['event'] == 'request_started'})
                    used += calls
                results = rows(target / 'results.jsonl')
                identity['runs'].append({'round': index, 'workers': workers, 'directory': target.name,
                                         'calls': calls, 'seconds': sum(r['elapsed_seconds'] for r in results)})
                identity['used_requests'] = used
                write(output / 'comparison.json', identity)
                print(f'END {target.name}: {calls} calls, {identity["runs"][-1]["seconds"]:.2f}s', flush=True)
                if result['status'] != 'completed' or len(results) != 4 or any(r['status'] != 'ok' for r in results):
                    raise ValueError('Incomplete PDF run; evidence retained, no automatic resubmission')
                if any(e.get('statusCode') == 429 for e in events):
                    raise ValueError('Rate or quota limit observed; stop the paired comparison')
        identity['status'] = 'completed'
    except BaseException as error:
        identity.update(status='stopped', error_type=type(error).__name__)
        raise
    finally:
        identity.update(used_requests=used, elapsed_seconds=time.monotonic() - started)
        write(output / 'comparison.json', identity)
    return identity


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('data', 'config', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    result = run(args.data, read(args.config), args.output, execute=args.execute)
    print(result.get('status', result))
