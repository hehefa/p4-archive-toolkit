import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from .core import *

def backup(db,local_dir,target):
    target=Path(target)
    if target.exists():raise ValueError('refuse to overwrite backup')
    target.parent.mkdir(parents=True,exist_ok=True)
    root=Path(local_dir).resolve()
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        tmp=Path(tmp);database=tmp/'vault.sqlite3'
        dest=sqlite3.connect(database)
        try:db.backup(dest)
        finally:dest.close()
        files={'vault.sqlite3':database}
        for sub in ('snapshots','research_snapshots'):
            folder=root/sub
            if folder.exists():
                for p in folder.rglob('*'):
                    if p.is_file() and not p.is_symlink():files[str(p.relative_to(root))]=p
        if (root/'settings.json').exists():files['settings.json']=root/'settings.json'
        manifest={name:{'sha256':file_hash(p),'bytes':p.stat().st_size} for name,p in files.items()}
        staged=tmp/'archive.zip'
        with zipfile.ZipFile(staged,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for name,p in files.items():
                z.write(p,name)
                if file_hash(p)!=manifest[name]['sha256']:raise ValueError('backup source changed')
            z.writestr('BACKUP_MANIFEST.json',canonical({'format':'p4-backup-v1','created_at':now(),'files':manifest}))
        staged.replace(target)
    return {'path':str(target),'sha256':file_hash(target),'files':len(files),'media_included':False}

def restore(archive,destination):
    destination=Path(destination)
    if destination.exists():raise ValueError('restore destination must not exist')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if len(names)!=len(set(names)):raise ValueError('duplicate backup entries')
        for i in z.infolist():
            p=Path(i.filename)
            if p.is_absolute() or '..' in p.parts or i.is_dir() or ((i.external_attr>>16)&0o170000)==0o120000:raise ValueError('unsafe backup member')
        manifest=json.loads(z.read('BACKUP_MANIFEST.json'))
        if manifest.get('format')!='p4-backup-v1':raise ValueError('invalid backup format')
        if set(names)!=set(manifest['files'])|{'BACKUP_MANIFEST.json'}:raise ValueError('backup members mismatch')
        with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
            staging=Path(staging);out=staging/'restored';out.mkdir()
            for name,entry in manifest['files'].items():
                if z.getinfo(name).file_size!=entry['bytes']:raise ValueError('backup length mismatch')
                p=out/name;p.parent.mkdir(parents=True,exist_ok=True)
                with z.open(name) as src,p.open('wb') as dst:shutil.copyfileobj(src,dst)
                if file_hash(p)!=entry['sha256']:raise ValueError('backup hash mismatch')
            test=connect(out/'vault.sqlite3')
            try:
                report=validate(test)
                if not report['ok']:raise ValueError('restored database validation failed')
            finally:test.close()
            out.rename(destination)
    return {'path':str(destination),'validated':True,'files':len(manifest['files'])}
