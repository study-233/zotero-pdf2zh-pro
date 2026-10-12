"""Collect checked-in and Cargo-shipped license texts for the locked Windows build."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def generate(metadata, skia, output):
    sections=['# Windows control center third-party notices\n',
              'Slint is used under GPL-3.0-only. This AGPL-3.0 project includes the linked libraries listed below. '
              'The inventory conservatively includes build dependencies. Versions are locked in windows-app/Cargo.lock.\n']
    for package in sorted(metadata['packages'],key=lambda p:(p['name'],p['version'])):
        if package['source'] is None:continue
        directory=Path(package['manifest_path']).parent
        files=[p for p in directory.iterdir() if p.is_file() and p.name.lower().startswith(('license','licence','copying','notice'))]
        if (directory/'LICENSES').is_dir():files += [p for p in (directory/'LICENSES').rglob('*') if p.is_file()]
        if not files:
            vcs=json.loads((directory/'.cargo_vcs_info.json').read_text(encoding='utf-8'))
            files=list((ROOT/'windows-app/licenses'/vcs['git']['sha1']).glob('*'))
        if not files:raise ValueError(f"Missing license text: {package['name']} {package['version']}")
        sections.append(f"## {package['name']} {package['version']}\n\nDeclared license: {package.get('license','unspecified')}\n\nSource: {package.get('repository','')}\n")
        for path in sorted(files):
            sections.append(f"### {path.name}\n\n```text\n{path.read_text(encoding='utf-8',errors='replace')}\n```\n")
    for directory in [skia]+sorted((skia/'third_party/externals').iterdir()):
        if not directory.is_dir():continue
        files=[p for p in directory.iterdir() if p.is_file() and p.name.lower().startswith(('license','licence','copying','notice','patents'))]
        if directory.name=='libjpeg-turbo':files.append(directory/'README.ijg')
        sections.append(f'## Skia source: {directory.name}\n')
        for path in sorted(set(files)):
            if path.is_file():sections.append(f"### {path.name}\n\n```text\n{path.read_text(encoding='utf-8',errors='replace')}\n```\n")
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text('\n'.join(sections),encoding='utf-8')
    print(f'Windows license inventory: {output}')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skia-source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--metadata',type=Path)
    args=parser.parse_args()
    if args.metadata:
        metadata=json.loads(args.metadata.read_text(encoding='utf-8-sig'))
    else:
        result=subprocess.run(['cargo','+stable-x86_64-pc-windows-msvc','metadata','--locked','--offline','--format-version','1','--filter-platform','x86_64-pc-windows-msvc','--manifest-path',str(ROOT/'windows-app/Cargo.toml')],check=True,capture_output=True,encoding='utf-8')
        metadata=json.loads(result.stdout)
    generate(metadata,args.skia_source,args.output)
