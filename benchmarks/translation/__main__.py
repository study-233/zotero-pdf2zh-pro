"""CLI. run/resume require --execute; all other commands avoid generation APIs."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import unittest
from pathlib import Path

from .common import DATASET, DEFAULT_DATA, DEFAULT_RUNS, HERE, read, write


def parser():
    p = argparse.ArgumentParser(description='Versioned English-to-Chinese translation evaluation')
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor')
    direct = commands.add_parser('direct', help='Isolated ChatGPT OAuth / Codex P0 comparison').add_subparsers(dest='action', required=True)
    from .chatgpt_auth import DEFAULT_AUTH
    for action in ('login', 'status', 'models', 'prepare', 'run', 'report', 'validate'):
        sub = direct.add_parser(action)
        sub.add_argument('--auth-dir', type=Path, default=DEFAULT_AUTH)
        if action in {'prepare', 'run', 'report', 'validate'}:
            sub.add_argument('--output', type=Path, required=True)
        if action in {'prepare', 'models', 'validate'}:
            sub.add_argument('--model', default='gpt-6-luna')
            sub.add_argument('--effort', default='low')
        if action in {'prepare', 'validate'}:
            sub.add_argument('--data', type=Path, default=DEFAULT_DATA / DATASET)
            sub.add_argument('--cli-path', required=True)
            sub.add_argument('--rounds', type=int, default=3)
            sub.add_argument('--max-requests', type=int, default=260 if action == 'validate' else 60)
            sub.add_argument('--max-seconds', type=int, default=5400 if action == 'validate' else 1800)
        if action in {'run', 'validate'}:
            sub.add_argument('--execute', action='store_true')
    dataset = commands.add_parser('dataset').add_subparsers(dest='action', required=True)
    for action in ('verify', 'fetch', 'materialize', 'export', 'import', 'legacy-import', 'report'):
        sub = dataset.add_parser(action)
        sub.add_argument('--data', type=Path, default=DEFAULT_DATA / DATASET)
        if action == 'verify':
            sub.add_argument('--metadata-only', action='store_true')
        if action in {'export', 'import'}:
            sub.add_argument('--archive', type=Path, required=True)
        if action == 'legacy-import':
            sub.add_argument('--source', type=Path, required=True)
        if action == 'report':
            sub.add_argument('--output', type=Path, required=True)
    for action in ('run', 'resume'):
        sub = commands.add_parser(action)
        sub.add_argument('--suite', choices=('offline', 'quick', 'model', 'selection', 'pdf-quick', 'pdf-full'), default='quick')
        sub.add_argument('--data', type=Path, default=DEFAULT_DATA / DATASET)
        sub.add_argument('--config', type=Path)
        sub.add_argument('--output', type=Path)
        sub.add_argument('--execute', action='store_true')
        sub.add_argument('--resolve-uncertain', action='store_true')
        sub.add_argument('--candidates', type=Path, help='Source-anchored diagnostic recipe; selection only, separate from frozen scores')
    review = commands.add_parser('review').add_subparsers(dest='action', required=True)
    sub = review.add_parser('prepare')
    sub.add_argument('runs', nargs='+', type=Path)
    sub.add_argument('--output', type=Path, required=True)
    sub.add_argument('--seed', type=int, default=61013)
    sub = review.add_parser('import')
    sub.add_argument('--packet', type=Path, required=True)
    sub.add_argument('--reviews', type=Path, required=True)
    sub = review.add_parser('audit')
    sub.add_argument('run', type=Path)
    sub.add_argument('--import-file', type=Path)
    sub = review.add_parser('new-round')
    sub.add_argument('run', type=Path)
    sub.add_argument('--archive-as', required=True)
    sub = review.add_parser('pdf-prepare')
    sub.add_argument('run', type=Path)
    sub.add_argument('--output', type=Path, required=True)
    sub = review.add_parser('pdf-import')
    sub.add_argument('--packet', type=Path, required=True)
    sub.add_argument('--reviews', type=Path, required=True)
    sub = commands.add_parser('report')
    sub.add_argument('runs', nargs='+', type=Path)
    sub.add_argument('--output', type=Path, required=True)
    sub = commands.add_parser('compare')
    sub.add_argument('runs', nargs='+', type=Path)
    sub.add_argument('--against', nargs='+', type=Path, required=True)
    sub.add_argument('--output', type=Path)
    sub = commands.add_parser('stability')
    sub.add_argument('runs', nargs='+', type=Path)
    sub.add_argument('--output', type=Path)
    maintenance = commands.add_parser('maintenance', help='Offline review closeout verification/report').add_subparsers(dest='action', required=True)
    for action in ('verify', 'report'):
        sub = maintenance.add_parser(action)
        sub.add_argument('--records', type=Path, required=True)
        sub.add_argument('--evidence-root', type=Path, required=action == 'report')
        if action == 'report':
            sub.add_argument('--output', type=Path, required=True)
    baseline = commands.add_parser('baseline').add_subparsers(dest='action', required=True)
    for action in ('list', 'promote'):
        sub = baseline.add_parser(action)
        sub.add_argument('--registry', type=Path, default=DEFAULT_RUNS.parent / 'baselines')
        if action == 'promote':
            sub.add_argument('run', type=Path)
            sub.add_argument('--name', required=True)
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        value = dispatch(args)
        if value is not None:
            print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(f'Benchmark error: {error}', file=sys.stderr)
        return 2


def dispatch(args):
    if args.command == 'maintenance':
        from . import maintenance
        if args.action == 'verify':
            return maintenance.verify(args.records, args.evidence_root)
        return maintenance.report(args.records, args.evidence_root, args.output)
    if args.command == 'direct':
        from . import direct_compare
        from .chatgpt_auth import AuthStore, login
        if args.action == 'validate':
            from .direct_validation import validate
            return validate(args.data, args.output, cli_path=args.cli_path, auth_directory=args.auth_dir,
                model=args.model, effort=args.effort, rounds=args.rounds, max_requests=args.max_requests,
                max_seconds=args.max_seconds, execute=args.execute)
        if args.action == 'login':
            return login(args.auth_dir, ready=lambda url: print(json.dumps({'login_url': url, 'expires_in_seconds': 900}), flush=True))
        if args.action == 'status':
            store = AuthStore(args.auth_dir)
            try:
                return store.status()
            finally:
                store.client.close()
        if args.action == 'models':
            from .chatgpt_direct import DirectProvider
            provider = DirectProvider(model=args.model, reasoning_effort=args.effort, auth_directory=args.auth_dir)
            try:
                return provider.list_models()
            finally:
                provider.close()
        if args.action == 'prepare':
            return direct_compare.prepare(args.data, args.output, model=args.model, effort=args.effort,
                cli_path=args.cli_path, rounds=args.rounds, max_requests=args.max_requests, max_seconds=args.max_seconds)
        if args.action == 'run':
            return direct_compare.run(args.output, args.auth_dir, execute=args.execute)
        return direct_compare.make_report(args.output)
    from . import corpus, legacy, report, review, runner
    if args.command == 'doctor':
        return {'python': sys.version.split()[0], 'dependencies': {name: importlib.util.find_spec(name) is not None for name in ('httpx', 'fitz', 'psutil')},
                'cli': {name: shutil.which(name) for name in ('codex', 'lms')}, 'live_calls': False}
    if args.command == 'dataset':
        if args.action == 'verify':
            return corpus.verify(HERE / 'datasets' / DATASET if args.metadata_only else args.data, metadata_only=args.metadata_only)
        if args.action == 'report':
            return report.dataset_report(args.data, args.output)
        if args.action == 'fetch':
            return corpus.fetch_sources(args.data)
        if args.action == 'materialize':
            return corpus.materialize(args.data)
        if args.action == 'export':
            return corpus.export_package(args.data, args.archive)
        if args.action == 'import':
            return corpus.import_package(args.archive, args.data)
        return legacy.import_legacy(args.source, args.data)
    if args.command in {'run', 'resume'}:
        if args.candidates and args.suite != 'selection':
            raise ValueError('--candidates requires --suite selection')
        if args.suite == 'offline':
            verified = corpus.verify(HERE / 'datasets' / DATASET, metadata_only=True)
            suite = unittest.defaultTestLoader.discover(str(HERE / 'tests'), pattern='test_*.py')
            result = unittest.TextTestRunner(verbosity=1).run(suite)
            if not result.wasSuccessful():
                raise ValueError('Offline benchmark checks failed')
            return {**verified, 'tests': result.testsRun, 'live_calls': False}
        if not args.config or not args.output:
            raise ValueError('--config and --output are required')
        config = read(args.config)
        if args.suite.startswith('pdf-'):
            from .product import pdf_jobs, run_pdf
            from .providers import validate_config
            validate_config(config)
            verified = corpus.verify(args.data)
            if not args.execute:
                return {**verified, 'suite': args.suite, 'documents': pdf_jobs(args.data, args.suite), 'limits': config['limits'], 'executes_model': False}
            return run_pdf(args.data, config, args.suite, args.output, resume=args.command == 'resume')
        if not args.execute:
            return runner.preflight(args.data, config, args.suite, args.candidates)
        return runner.run(args.data, config, args.suite, args.output, resume=args.command == 'resume', resolve_uncertain=args.resolve_uncertain, candidates=args.candidates)
    if args.command == 'review':
        if args.action == 'new-round':
            return review.new_round(args.run, args.archive_as)
        if args.action.startswith('pdf-'):
            from . import pdf_review
            return pdf_review.prepare(args.run, args.output) if args.action == 'pdf-prepare' else pdf_review.import_reviews(args.packet, args.reviews)
        if args.action == 'prepare':
            return review.prepare(args.runs, args.output, args.seed)
        if args.action == 'import':
            return review.import_reviews(args.packet, args.reviews)
        if args.import_file:
            return review.import_audit(args.run, args.import_file)
        return review.audit(args.run)
    if args.command == 'report':
        return {'report': report.report(args.runs, args.output)}
    if args.command == 'compare':
        result = report.compare(args.against, args.runs)
        if args.output:
            write(args.output, result)
        return result
    if args.command == 'stability':
        result = report.stability(args.runs)
        if args.output:
            write(args.output, result)
        return result
    if args.command == 'baseline':
        if args.action == 'list':
            return [read(p) for p in sorted(args.registry.glob('*.json'))]
        return {'baseline': review.promote(args.run, args.registry, args.name)}


if __name__ == '__main__':
    raise SystemExit(main())
