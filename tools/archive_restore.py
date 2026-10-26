"""Offline, hash-checked restoration of an authorized full research Release."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import zipfile


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise ValueError('invalid relative path')
    p = PurePosixPath(value)
    if p.is_absolute() or any(x in {'', '.', '..'} for x in value.split('/')):
        raise ValueError('unsafe relative path')
    return p


def load_verified(assets, expected, manifest_name="p4-manifest.json"):
    assets = Path(assets).resolve()
    if not re.fullmatch('[a-f0-9]{64}', expected):
        raise ValueError('expected manifest SHA256 is required')
    if len(relative(manifest_name).parts) != 1:
        raise ValueError('manifest must be an asset filename')
    p = assets / manifest_name
    if p.is_symlink() or sha256(p) != expected:
        raise ValueError('manifest hash mismatch')
    manifest = json.loads(p.read_text())
    if manifest.get('format') != 'p4-full-research-release-v1':
        raise ValueError('unsupported format')
    names = set()
    for entry in manifest['assets']:
        name = entry['name']
        if len(relative(name).parts) != 1 or name in names:
            raise ValueError('duplicate or nested asset')
        names.add(name)
        p = assets / name
        if p.is_symlink() or p.stat().st_size != entry['bytes'] or sha256(p) != entry['sha256']:
            raise ValueError('asset hash/size mismatch: ' + name)
    if manifest['catalogue'] not in names or manifest['vault'] not in names:
        raise ValueError('missing catalogue or vault asset')
    rows = [json.loads(line) for line in (assets / manifest['catalogue']).read_text().splitlines()]
    seen = set()
    for row in rows:
        if len(relative(row['root']).parts) != 1:
            raise ValueError('invalid root')
        key = str(relative(row['root'] + '/' + str(relative(row['path']))))
        if key in seen:
            raise ValueError('duplicate destination')
        seen.add(key)
        sha = row['sha256']
        if not re.fullmatch('[a-f0-9]{64}', sha):
            raise ValueError('invalid object hash')
        obj = manifest['objects'][sha]
        if obj['asset'] not in names or obj['member'] != 'objects/' + sha or obj['bytes'] != row['bytes']:
            raise ValueError('object map mismatch')
    if set(manifest['objects']) != {r['sha256'] for r in rows}:
        raise ValueError('unreferenced/missing objects')
    return manifest, rows


def restore(assets, destination, expected, manifest_name="p4-manifest.json"):
    assets = Path(assets).resolve()
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('destination must not exist')
    # Complete asset and path validation before creating any output.
    manifest, rows = load_verified(assets, expected, manifest_name)
    for parent in destination.absolute().parents:
        if parent.is_symlink():
            raise ValueError('destination parent is a symlink')
    destination.mkdir(parents=True, exist_ok=False)
    cache = destination / '_objects'
    cache.mkdir()
    by_asset = {}
    for sha, obj in manifest['objects'].items():
        by_asset.setdefault(obj['asset'], []).append((sha, obj))
    for name, objects in by_asset.items():
        with zipfile.ZipFile(assets / name) as archive:
            members = archive.namelist()
            if len(members) != len(set(members)) or set(members) != {obj['member'] for _, obj in objects}:
                raise ValueError('unexpected archive members')
            for sha, obj in objects:
                info = archive.getinfo(obj['member'])
                if info.file_size != obj['bytes'] or (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('object size or symlink mismatch')
                p = cache / sha
                with archive.open(info) as src, p.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                if sha256(p) != sha:
                    raise ValueError('object content mismatch')
    for row in rows:
        p = destination / 'sources' / row['root'] / row['path']
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cache / row['sha256'], p)
    shutil.copyfile(assets / manifest['vault'], destination / 'p4-vault-complete.zip')
    shutil.copyfile(assets / manifest_name, destination / manifest_name)
    shutil.rmtree(cache)
    result = {'files_restored': len(rows), 'unique_objects_verified': len(manifest['objects']), 'vault_copied': True}
    (destination / 'RESTORE_PROOF.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('assets', type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--manifest-file', default='p4-manifest.json')
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--query', help='Search source filenames after verifying downloaded assets')
    args = parser.parse_args()
    if args.query is not None:
        _, rows = load_verified(args.assets, args.manifest_sha256, args.manifest_file)
        print(json.dumps([r for r in rows if args.query.casefold() in (r['root'] + '/' + r['path']).casefold()], ensure_ascii=False, indent=2))
    elif args.destination:
        print(json.dumps(restore(args.assets, args.destination, args.manifest_sha256, args.manifest_file)))
    else:
        parser.error('provide --destination or --query')


if __name__ == '__main__':
    main()
