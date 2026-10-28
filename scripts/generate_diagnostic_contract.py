"""Generate both diagnostic boundaries and reject unregistered feature changes."""
from __future__ import annotations

import argparse
import ast
import copy
import json
import pprint
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path('shared/diagnostics/contract.json')
PYTHON = Path('server/diagnostic_contract.py')
TYPESCRIPT = Path('plugin/src/modules/diagnosticContract.ts')


def validate(contract, root=ROOT):
    baseline = json.loads((root / 'shared/diagnostics/compatibility-v1.json').read_text(encoding="utf-8"))
    if contract['schemaVersion'] != 1 or contract['supportedSchemaVersions'] != [1]:
        raise ValueError('A new major format requires an explicit adapter and compatibility tests.')
    if type(contract['schemaRevision']) is not int or contract['schemaRevision'] < 1:
        raise ValueError('schemaRevision must be a positive integer.')
    for group in ('numeric', 'flags', 'failedParagraphFields'):
        if not set(baseline[group]) <= set(contract[group]):
            raise ValueError(f'Cannot remove shipped v1 fields: {group}')
    for name, values in baseline['enums'].items():
        if not set(values) <= set(contract['enums'].get(name, [])):
            raise ValueError(f'Cannot remove shipped v1 values: {name}')
    groups = [set(contract[key]) for key in
              ('numeric', 'flags', 'enums', 'identifiers', 'labels', 'strings', 'timestamps', 'containers', 'countMaps', 'opaqueIdentifiers', 'identifierLists')]
    for index, group in enumerate(groups):
        if any(group & other for other in groups[index + 1:]):
            raise ValueError('Diagnostic field categories must not overlap.')
    forbidden = {'apiKey', 'authorization', 'prompt', 'response', 'text', 'fileName', 'apiUrl', 'reason'}
    if set.union(*groups) & forbidden:
        raise ValueError('Raw document/provider fields cannot enter the diagnostic contract.')
    if not set(baseline['providerCodes']) <= set(contract['providerErrors']):
        raise ValueError('Cannot remove shipped v1 provider codes.')
    for registry in ('validationErrors', 'providerErrors'):
        for code, definition in contract[registry].items():
            if not code.isidentifier() or not {'stage', 'retryable', 'requiredEvidence', 'suggestion'} <= definition.keys():
                raise ValueError(f'Incomplete diagnostic error definition: {code}')
    # New health capabilities must explicitly declare their diagnostic coverage.
    tree = ast.parse((root / 'server/server.py').read_text(encoding="utf-8"))
    capabilities = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == 'capabilities' and isinstance(value, ast.Dict):
                    capabilities.update(k.value for k in value.keys if isinstance(k, ast.Constant))
    if capabilities != set(contract['productCapabilities']):
        raise ValueError(f'Update productCapabilities coverage: {sorted(capabilities ^ set(contract["productCapabilities"]))}')
    for feature in contract['productCapabilities'].values():
        if feature['coverage'] not in {'task_events', 'request_metrics', 'availability_only'}:
            raise ValueError('Unknown feature coverage. Do not claim unsupported collection.')
    known_fields = set.union(*groups) | {'providerCode', 'validationCode', 'failedParagraphs'}
    for definition in (*contract['validationErrors'].values(), *contract['providerErrors'].values()):
        if not set(definition['requiredEvidence']) <= known_fields:
            raise ValueError('Register required error evidence fields.')
    for key, pattern in contract['opaqueIdentifiers'].items():
        re.compile(pattern)
        if key not in known_fields:
            raise ValueError('Unregistered opaque identifier.')
    if not set(contract['identifierLists'].values()) <= set(contract['opaqueIdentifiers']):
        raise ValueError('Identifier lists must reference registered opaque identifiers.')
    for kind, fields in contract['evidenceFields'].items():
        if not set(fields) <= known_fields or not {'taskId', 'attempt', 'executionId'} <= set(fields):
            raise ValueError(f'Unregistered or incomplete evidence fields: {kind}')
    if set(contract['diagnosisRules']) != set(contract['enums']['findingCode']):
        raise ValueError('Every diagnosis rule needs a registered findingCode.')
    for rule in contract['diagnosisRules'].values():
        if not rule.get('title') or not rule.get('suggestion'):
            raise ValueError('Every diagnosis rule needs an explanation and suggestion.')
    for event, definition in contract['events'].items():
        if not set(definition['requiredFields']) <= known_fields:
            raise ValueError(f'Unregistered required event fields: {event}')
    roots = [root / 'server/babeldoc', root / 'server/pdf2zh_pro']
    paths = [*root.joinpath('server').glob('*.py'), *(p for folder in roots for p in folder.rglob('*.py'))]
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Call) and node.args and isinstance(node.func, ast.Attribute):
                target = ast.unparse(node.func)
                if target.endswith('.diagnostics.record') or target in {'store.record', 'self.emit_record'}:
                    event = node.args[0]
                    if isinstance(event, ast.Constant) and isinstance(event.value, str) and event.value not in contract['events']:
                        raise ValueError(f'Unregistered diagnostic event at {path.relative_to(root)}:{node.lineno}')
                    if len(node.args) > 1 and isinstance(node.args[1], ast.Dict):
                        for key in node.args[1].keys:
                            if isinstance(key, ast.Constant) and key.value not in known_fields:
                                raise ValueError(f'Unregistered diagnostic field at {path.relative_to(root)}:{node.lineno}')
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'InvalidTranslation' and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                    and node.args[0].value not in contract['validationErrors']):
                raise ValueError(f'Unregistered validation code at {path.relative_to(root)}:{node.lineno}')


def render(contract):
    data = copy.deepcopy(contract)
    data['enums']['validationCode'] = sorted(data['validationErrors'])
    data['enums']['providerCode'] = sorted(data['providerErrors'])
    warning = 'Generated by scripts/generate_diagnostic_contract.py; edit shared/diagnostics/contract.json.'
    return {
        PYTHON: '# ' + warning + '\nCONTRACT = ' + pprint.pformat(data, width=100, sort_dicts=True) + '\n',
        TYPESCRIPT: '// ' + warning + '\nexport const diagnosticContract = ' + json.dumps(data, ensure_ascii=False, indent=4) + ' as const;\n'
                    + 'export type DiagnosticValidationCode = keyof typeof diagnosticContract.validationErrors;\n'
                    + 'export type DiagnosticProtocol = (typeof diagnosticContract.enums.protocol)[number];\n'
                    + 'export type DiagnosticEvent = keyof typeof diagnosticContract.events;\n',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    contract = json.loads((ROOT / SOURCE).read_text(encoding="utf-8"))
    validate(contract)
    stale = []
    for relative, content in render(contract).items():
        path = ROOT / relative
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(str(relative))
        else:
            path.write_text(content, encoding='utf-8')
    if stale:
        parser.exit(1, 'Regenerate diagnostic contract: ' + ', '.join(stale) + '\n')
    print('Diagnostic contract and feature coverage checks passed.')


if __name__ == '__main__':
    main()
