"""Extract the pinned Skia source archive without requiring Windows symlink privileges.

Links are copied only when their resolved targets are existing files inside the archive root.
All archive paths are checked before anything is written.
"""
import argparse
import hashlib
from pathlib import Path, PurePosixPath
import shutil
import tarfile


def unpack(archive: Path, destination: Path) -> Path:
    destination = destination.resolve()
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        prefixes = {PurePosixPath(m.name).parts[0] for m in members if m.name}
        if len(prefixes) != 1:
            raise ValueError('Expected one Skia source root')
        source_root = destination / prefixes.pop()
        links = []
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or '\\' in member.name or ':' in member.name:
                raise ValueError(f'Unsafe archive path: {member.name}')
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError(f'Unsupported archive entry: {member.name}')
            path = (destination / member.name).resolve()
            if not path.is_relative_to(source_root):
                raise ValueError(f'Archive entry escapes source root: {member.name}')
            if member.issym() or member.islnk():
                target = ((path.parent / member.linkname) if member.issym() else (destination / member.linkname)).resolve()
                if not target.is_relative_to(source_root):
                    raise ValueError(f'Link escapes source root: {member.name}')
                links.append((path, target))
        for member in members:
            path = destination / member.name
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as stream, path.open('wb') as output:
                    shutil.copyfileobj(stream, output)
        while links:
            remaining = []
            for path, target in links:
                if target.is_file():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(target, path)
                else:
                    remaining.append((path, target))
            if len(remaining) == len(links):
                raise ValueError(f'Unresolved source links: {remaining}')
            links = remaining
    return source_root


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--sha256', default='30c8b51a0a32fae63562c303bdc91605519a9598951422610e3fb8685b3c4f6f', help='Expected hash of the pinned m153-0.101.2 source archive')
    args = parser.parse_args()
    digest = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    if digest != args.sha256:
        raise ValueError('Skia source archive SHA-256 mismatch')
    print('Skia archive SHA-256:', digest)
    print('Skia source root:', unpack(args.archive, args.destination))
