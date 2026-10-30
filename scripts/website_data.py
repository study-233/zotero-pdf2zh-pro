"""Shared, read-only benchmark preparation for both website builders."""
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / 'benchmarks/translation/site'
PUBLICATION = ROOT / 'benchmarks/translation/publications/2026-10-26-v2'
PILOT_PUBLICATION = ROOT / 'benchmarks/translation/publications/2026-10-26-interface-pilot-v1'
REGISTRY = ROOT / 'benchmarks/translation/publications/2026-10-27-unified-v1'
EXPERIMENT_PUBLICATION = ROOT / 'benchmarks/translation/publications/2026-10-27-interface-formal-v1'


def load_benchmark():
    benchmark = json.loads((REGISTRY / 'summary.json').read_text(encoding='utf-8'))
    if benchmark.get('experiment'):
        experiment = json.loads((EXPERIMENT_PUBLICATION / 'summary.json').read_text(encoding='utf-8'))
        if experiment.get('status') != 'audited_formal':
            raise ValueError('Only an adjudicated and audited experiment may appear on the comparison page')
    if (PILOT_PUBLICATION / 'summary.json').exists():
        pilot = json.loads((PILOT_PUBLICATION / 'summary.json').read_text(encoding='utf-8'))
        if pilot.get('status') != 'audited_pilot':
            raise ValueError('Only an audited pilot may appear on the comparison page')
    # Keep one audited formal configuration per model and both measured GPT
    # access sources. The complete registry remains available for download.
    overview_ids = [
        'z-ai/glm-5.3-flash', '20261027/command-responses-none',
        'claude-haiku-5-5', '20261027/deepseek-chat-low',
        'xiaomi/mimo-v2.6-flash', 'Qwen/Qwen3.7-Flash',
        'minimax/minimax-m3.1-flash', 'Qwen/Qwen3.8-Flash',
        '20261027/mimo-responses-low', '20261027/account-responses-low',
    ]
    records = {model['id']: model for model in benchmark['models']}
    brand_names = {
        'z-ai/glm-5.3-flash': 'zhipu', 'gpt-6-luna': 'openai',
        'claude-haiku-5-5': 'anthropic', 'deepseek/deepseek-v4.1-flash': 'deepseek',
        'xiaomi/mimo-v2.6-flash': 'xiaomi', 'xiaomi/mimo-v2.6-pro': 'xiaomi',
        'Qwen/Qwen3.7-Flash': 'alibaba', 'Qwen/Qwen3.8-Flash': 'alibaba',
        'minimax/minimax-m3.1-flash': 'minimax',
    }
    channel_sources = {
        'Command Code': {'label': 'Command Code 套餐', 'icon': 'channels/command-code.svg'},
        'ChatGPT subscription': {'label': '插件 GPT 账号', 'icon': 'brands/openai.svg'},
    }
    overview = {**benchmark, 'models': [
        {**records[key], 'name': records[key]['model_name'] +
         (' · 插件账号' if records[key]['channel'] == 'ChatGPT subscription' else ' · Command Code')
         if records[key]['model_id'] == 'gpt-6-luna' else records[key]['model_name'],
         'chart_color': index,
         'chart_logo': brand_names[records[key]['model_id']],
         'source': channel_sources[records[key]['channel']]}
        for index, key in enumerate(overview_ids)
    ]}
    if any(model['kind'] != 'formal' or model['samples'] != 240 for model in overview['models']):
        raise ValueError('The model overview requires 240-sample formal results')
    overview['date_label'] = '2026-10-23 — 27'
    overview['model_count'] = len({model['model_id'] for model in overview['models']})
    overview['sources'] = [channel_sources[channel] for channel in
                           dict.fromkeys(model['channel'] for model in overview['models'])]
    return benchmark, overview


def copy_benchmark_assets(output, benchmark):
    # Preserve the historical root URLs and publication overlay order.
    shutil.copytree(BENCHMARK, output, dirs_exist_ok=True)
    if benchmark.get('experiment'):
        shutil.copytree(EXPERIMENT_PUBLICATION, output / 'benchmark/experiment', dirs_exist_ok=True)
    if (PILOT_PUBLICATION / 'summary.json').exists():
        shutil.copytree(PILOT_PUBLICATION, output / 'benchmark/pilot', dirs_exist_ok=True)
    shutil.copytree(PUBLICATION, output / 'benchmark/data', dirs_exist_ok=True)
    shutil.copytree(PUBLICATION, output / 'benchmark/data/formal', dirs_exist_ok=True)
    shutil.copytree(REGISTRY, output / 'benchmark/data', dirs_exist_ok=True)
