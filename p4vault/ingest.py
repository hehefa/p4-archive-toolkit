"""Explicit snapshot adapters; never scan a drive or copy a media file."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from .core import *

MAX_INPUT=64*1024*1024

def read_json_stable(path):
    p=Path(path).resolve()
    if p.suffix.lower()!='.json': raise ValueError('only explicit JSON input allowed')
    before=p.stat()
    if before.st_size>MAX_INPUT: raise ValueError('snapshot exceeds 64 MiB')
    data=p.read_bytes(); after=p.stat()
    if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('source changed during read; request a fixed snapshot')
    return json.loads(data), data, hashlib.sha256(data).hexdigest()

def retain_snapshot(local_dir,raw,source,namespace):
    root=Path(local_dir); folder=root/'snapshots';folder.mkdir(parents=True,exist_ok=True)
    sha=hashlib.sha256(raw).hexdigest(); target=folder/(sha+'.json')
    if target.exists() and file_hash(target)!=sha:raise ValueError('snapshot content was modified')
    if not target.exists():target.write_bytes(raw)
    # Source paths are private; manifests live under ignored local/.
    manifest={'sha256':sha,'bytes':len(raw),'source':str(source),'namespace':namespace,'captured_at':now(),'media_copied':False}
    mp=folder/(sha+'.manifest.json')
    if not mp.exists():mp.write_text(canonical(manifest))
    return target,sha

def import_bundle(db,bundle,namespace,source,local_dir,raw=None):
    if not isinstance(bundle,dict) or bundle.get('format')!='p4-evidence-v1':raise ValueError('expected p4-evidence-v1 bundle')
    if not isinstance(bundle.get('entities'),list) or not isinstance(bundle.get('relations',[]),list):raise ValueError('invalid bundle arrays')
    raw=raw if raw is not None else canonical(bundle).encode()
    snapshot,sha=retain_snapshot(local_dir,raw,source,namespace)
    batch_id=stable_id('batch',namespace,sha)
    old=db.execute('SELECT report FROM import_batch WHERE id=?',(batch_id,)).fetchone()
    if old:return {'status':'already_imported','batch_id':batch_id,**json.loads(old[0])}
    report={'inserted':0,'updated':0,'unchanged':0,'relations':0,'snapshot_sha256':sha}
    ids=[e.get('id') for e in bundle['entities']]
    if len(ids)!=len(set(ids)):raise ValueError('duplicate entity IDs in input')
    relids=[e.get('id') for e in bundle.get('relations',[])]
    if len(relids)!=len(set(relids)):raise ValueError('duplicate relation IDs in input')
    with db:
        for record in bundle['entities']:
            report[put_entity(db,record,'Import '+namespace,batch_id)]+=1
            sid=str(record.get('metadata',{}).get('upstream_id',record['id']))
            oldalias=db.execute('SELECT entity_id FROM alias WHERE namespace=? AND source_id=?',(namespace,sid)).fetchone()
            if oldalias and oldalias[0]!=record['id']:raise ValueError('upstream identity remapping requires explicit migration')
            db.execute('INSERT OR IGNORE INTO alias VALUES(?,?,?)',(namespace,sid,record['id']))
        for relation in bundle.get('relations',[]):
            put_relation(db,relation,'Import '+namespace);report['relations']+=1
        check=validate(db)
        if check['errors']:raise ValueError('import validation failed: '+canonical(check['errors'][:10]))
        for e in bundle['entities']:
            q=e.get('reliability',{})
            if e['kind']=='segment' and not q.get('human_reviewed',False):enqueue(db,'listen_review',e['id'],'Machine evidence remains unreviewed',7)
            if e['kind']=='person' and e.get('metadata',{}).get('identity_status')!='confirmed':enqueue(db,'resolve_identity',e['id'],'Do not infer speaker identity from filename',3)
        for w in check['warnings']:
            if w['code'] in {'claim_without_source','casting_incomplete'}:enqueue(db,w['code'],w['id'],'Resolve validation finding',4)
        db.execute('INSERT INTO import_batch VALUES(?,?,?,?,?,?,?)',(batch_id,namespace,sha,str(source),str(snapshot),now(),canonical(report)))
        audit(db,'import',{'batch_id':batch_id,'namespace':namespace,'sha256':sha,'report':report})
    return {'status':'imported','batch_id':batch_id,**report}

def import_json(db,path,namespace,local_dir):
    bundle,raw,sha=read_json_stable(path)
    return import_bundle(db,bundle,namespace,str(Path(path).resolve()),local_dir,raw)

def permission(hold=False,basis='Imported local evidence; external publication scope not approved'):
    return {'state':'local_only','hold':bool(hold),'basis':basis}

def reliability(method='unknown',limitations=None):
    return {'method':method,'human_reviewed':False,'limitations':limitations or []}

def relation(s,p,o):return {'id':stable_id('rel',p,s+'\0'+o),'subject':s,'predicate':p,'object':o,'epistemic':'record','provenance':[]}

def import_catalog(db,path,local_dir):
    p=Path(path).resolve()
    src=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);src.row_factory=sqlite3.Row
    try:
        src.execute('PRAGMA query_only=ON');src.execute('BEGIN')
        cards=[dict(r) for r in src.execute('SELECT * FROM cards ORDER BY id')]
        sourceids=sorted({r['source_id'] for r in cards})
        if not sourceids:raise ValueError('catalog contains no registered scene cards')
        placeholders=','.join('?' for _ in sourceids)
        assets=[dict(r) for r in src.execute('SELECT * FROM assets WHERE id IN ('+placeholders+') ORDER BY id',sourceids)]
        docs=[dict(r) for r in src.execute('SELECT * FROM documents WHERE source_id IN ('+placeholders+') ORDER BY id',sourceids)]
        segs=[dict(r) for r in src.execute('SELECT * FROM segments WHERE source_id IN ('+placeholders+') ORDER BY id',sourceids)]
        totals={t:src.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('assets','documents','segments','cards')}
    finally:src.close()
    selected={'adapter':'legacy-catalog-v1','source':str(p),'selection':'registered scene cards and their source assets/documents/segments only','source_totals':totals,'assets':assets,'documents':docs,'segments':segs,'cards':cards}
    raw=canonical(selected).encode();sha=hashlib.sha256(raw).hexdigest()
    prov=[{'snapshot_sha256':sha,'source_catalog':str(p),'selection':'registered scene cards'}]
    ents=[];rels=[]
    for r in assets:
        ents.append({'id':r['id'],'kind':'asset','title':r['name'],'text':'','metadata':{'upstream_id':r['id'],'local_path':r['path'],'bytes':r['bytes'],'legacy_path_id':r['id'],'content_sha256':r['source_sha256'],'hash_status':'unknown' if not r['source_sha256'] else 'inherited','legacy_verification':r['verification'],'legacy_family_hint':r['family_hint']},'permission':permission(r['publication_hold'],r['hold_evidence'] or 'No GitHub grant'),'reliability':reliability('document_record',['Path ID is not content hash; family label is a hint']),'provenance':prov})
    for r in docs:
        id=stable_id('document','catalog-document',r['id'])
        ents.append({'id':id,'kind':'document','title':'机器转写来源 '+r['id'],'refs':[r['source_id']],'metadata':{'upstream_id':r['id'],'local_path':r['path'],'content_sha256':r['sha256'],'coverage_kind':r['coverage_kind'],'window_start':r['window_start'],'window_end':r['window_end'],'model':r['model']},'permission':permission(),'reliability':reliability('machine_asr',['Not human listening review']),'provenance':prov})
    for r in segs:
        id=stable_id('segment','catalog-segment',str(r['id']));doc=stable_id('document','catalog-document',r['document_id'])
        ents.append({'id':id,'kind':'segment','title':'源片段 '+str(r['id']),'text':r['text'],'source_id':r['source_id'],'start':r['start'],'end':r['end'],'time_basis':'source_zero','refs':[r['source_id'],doc],'epistemic':'quotation','reliability':reliability('machine_asr',['Machine transcript, not verified quotation','Already relative to source zero; no window offset added']),'permission':permission(),'metadata':{'upstream_id':'segment:'+str(r['id']),'quality_raw':r['quality']},'provenance':prov})
    for r in cards:
        id=stable_id('event','catalog-card',r['id'])
        ents.append({'id':id,'kind':'event','title':r['title'],'text':r['description'] or '', 'event_time':{'value':None,'precision':'unknown','source':'Legacy card has source time, no verified calendar date'},'refs':[r['source_id']],'metadata':{'upstream_id':'card:'+r['id'],'source_start':r['start'],'source_end':r['end'],'context':r['context'],'legacy_evidence':r['evidence']},'reliability':reliability('mixed',['Inherited scene card, no new continuous human review']),'permission':permission(r['publication_hold']),'provenance':prov})
        rels.append(relation(id,'source',r['source_id']))
    return import_bundle(db,{'format':'p4-evidence-v1','entities':ents,'relations':rels},'legacy-catalog-scene-cards',p,local_dir,raw)

def import_fixed25(db,path,local_dir):
    data,raw,sha=read_json_stable(path)
    if data.get('count')!=25 or len(data.get('records',[]))!=25:raise ValueError('fixed25 requires exactly 25 records')
    ents=[]
    for r in data['records']:
        sid=str(r['stable_id']);id=stable_id('coverage','fixed25',sid)
        ents.append({'id':id,'kind':'coverage','title':'固定25覆盖 #'+str(r['fixed_position']),'text':r.get('current_local_review_state',''),'metadata':{'upstream_id':sid,'record':r,'content_sha256':r.get('sha256'),'duration_seconds':r.get('duration_seconds'),'local_path':r.get('source_path')},'reliability':reliability('mixed',['Coverage is not work count','Machine/visual coverage does not mean accurate dialogue or human listening','Platform visibility is inherited observation, not refreshed']),'permission':permission(not r.get('public_release_approved',False)),'provenance':[{'snapshot_sha256':sha,'source':str(path),'pointer':'/records/'+str(r['fixed_position']-1)}]})
    return import_bundle(db,{'format':'p4-evidence-v1','entities':ents},'fixed25-method-endpoint',path,local_dir,raw)
