"""Check the explicit code-only release manifest; never discover data for upload."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
FORBIDDEN={'local','exports','backups','.git','.aws','.ssh','.codex','.agents'}

def inspect(root=ROOT):
    manifest=json.loads((root/'release-files.json').read_text());errors=[];total=0
    if manifest.get('scope')!='code-and-synthetic-only':errors.append('invalid manifest scope')
    names=[x['path'] for x in manifest['files']]
    if len(names)!=len(set(names)):errors.append('duplicate release entry')
    for entry in manifest['files']:
        name=entry['path'];rel=Path(name);p=root/rel
        if rel.is_absolute() or '..' in rel.parts or any(x in FORBIDDEN for x in rel.parts) or p.is_symlink() or p.suffix in {'.sqlite3','.sqlite','.db','.zip','.mp4','.mov','.wav','.m4a'}:
            errors.append('forbidden release member: '+name);continue
        if not p.is_file():errors.append('missing release member: '+name);continue
        raw=p.read_bytes();total+=len(raw)
        if hashlib.sha256(raw).hexdigest()!=entry['sha256'] or len(raw)!=entry['bytes']:errors.append('hash/length mismatch: '+name)
        if len(raw)>2*1024*1024:errors.append('unexpected large code member: '+name)
        # Credential/private path literals should not be in code or docs; generic patterns are fine.
        text=raw.decode('utf-8')
        if re.search(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)',text):errors.append('credential signature: '+name)
        if re.search(r'/(?:Users|Volumes)/[^\s\'"\\]*',text) and ('/Users/'+'hefa') in text:errors.append('private absolute user path: '+name)
    public_names=[]
    public_manifest=root/'public/manifest.json'
    if public_manifest.exists():
        try:
            pm=json.loads(public_manifest.read_text())
            if pm.get('format')!='p4-approved-public-release-v1' or pm.get('approval')!='explicit-user-approved' or pm.get('visibility')!='private' or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',pm.get('repository','')):
                errors.append('public payload lacks explicit private-repository approval')
            public_names=[e['path'] for e in pm['files']]
            if len(public_names)!=len(set(public_names)):errors.append('duplicate public payload entry')
            for e in pm['files']:
                rel=Path(e['path']);p=root/rel
                if rel.is_absolute() or '..' in rel.parts or rel.parts[0]!='public' or any(x in FORBIDDEN for x in rel.parts) or p.is_symlink() or p.suffix not in {'.json','.md'}:
                    errors.append('forbidden public payload: '+e['path']);continue
                b=p.read_bytes()
                if len(b)!=e['bytes'] or hashlib.sha256(b).hexdigest()!=e['sha256']:errors.append('public payload hash mismatch: '+e['path'])
                body=b.decode('utf-8')
                if re.search(r'/Users/|/Volumes/|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<![A-Za-z0-9])1[3-9]\d{9}(?![A-Za-z0-9])|(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})|\d+\s*号楼\s*\d+',body):errors.append('sensitive public payload: '+e['path'])
                total+=len(b)
            public_names.append('public/manifest.json')
        except (KeyError,ValueError,OSError,TypeError) as ex:errors.append('invalid public manifest: '+str(ex))
    if (root/'.git').exists():
        files=subprocess.check_output(['git','-C',str(root),'ls-files','-z'],text=True).split('\0')
        unexpected=set(filter(None,files))-set(names)-set(public_names)-{'release-files.json'}
        if unexpected:errors.append('tracked file outside allowlist: '+','.join(sorted(unexpected)))
    return {'ok':not errors,'errors':errors,'allowlisted_files':len(names)+1+len(public_names),'code_files':len(names)+1,'approved_public_files':len(public_names),'bytes_excluding_manifest':total,'private_data_allowed':False,'remote_write_performed':False}
if __name__=='__main__':
    result=inspect();print(json.dumps(result,ensure_ascii=False,indent=2));sys.exit(0 if result['ok'] else 1)
