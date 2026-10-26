"""Versioned evidence store. Standard library only; no network or model calls."""
import hashlib
import json
import math
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KINDS = {'project','person','role','casting','event','asset','source','segment','claim','document','coverage','publication','alignment'}
METHODS = {'unknown','machine_asr','machine_visual','human_listening','human_observation','document_record','inference','mixed'}
PERMISSIONS = {'unknown','local_only','public_approved','withdrawn'}
PREDICATES = {'part_of','performed_by','role','in_project','source','supports','contradicts','version_of','derived_from','same_as_candidate','located_at','describes','precedes','related_to'}
ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$')

def now(): return datetime.now(timezone.utc).isoformat()
def canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
def digest(value): return hashlib.sha256(canonical(value).encode()).hexdigest()
def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()
def stable_id(kind, namespace, source_id): return kind+':'+str(uuid.uuid5(uuid.NAMESPACE_URL,namespace+'\0'+str(source_id)))
def check_id(value):
    if not isinstance(value,str) or not ID_RE.fullmatch(value): raise ValueError('invalid stable ID')
def connect(path):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path); db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON'); db.execute('PRAGMA busy_timeout=5000')
    migrate(db)
    return db

def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS schema_migration(version INTEGER PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL)')
    applied={r['version']:r['sha256'] for r in db.execute('SELECT * FROM schema_migration')}
    paths=sorted((ROOT/'migrations').glob('*.sql'))
    known={int(p.name.split('_')[0]) for p in paths}
    if set(applied)-known: raise ValueError('database schema newer than this application')
    for p in paths:
        version=int(p.name.split('_')[0]); sha=file_hash(p)
        if version in applied:
            if applied[version]!=sha: raise ValueError('applied migration changed: '+p.name)
            continue
        try:
            db.execute('BEGIN IMMEDIATE')
            for sql in p.read_text().split(';'):
                if sql.strip(): db.execute(sql)
            db.execute('INSERT INTO schema_migration VALUES(?,?,?)',(version,sha,now()))
            db.commit()
        except Exception:
            db.rollback(); raise

def audit(db, action, detail):
    previous=db.execute('SELECT hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
    previous=previous[0] if previous else ''
    at=now(); text=canonical(detail)
    h=digest([previous,at,action,text])
    db.execute('INSERT INTO audit(at,action,detail,previous_hash,hash) VALUES(?,?,?,?,?)',(at,action,text,previous,h))

def normalized(record):
    e=dict(record); check_id(e.get('id'))
    if e.get('kind') not in KINDS: raise ValueError('unsupported entity kind')
    if not isinstance(e.get('title'),str) or not e['title'].strip(): raise ValueError('title required')
    e.setdefault('text',''); e.setdefault('metadata',{})
    if not isinstance(e['text'],str) or not isinstance(e['metadata'],dict): raise ValueError('invalid text or metadata')
    e.setdefault('epistemic','record')
    if e['epistemic'] not in {'record','observation','inference','quotation','unknown'}: raise ValueError('invalid epistemic status')
    e.setdefault('reliability',{'method':'unknown','human_reviewed':False,'limitations':[]})
    q=e['reliability']
    if not isinstance(q,dict) or q.get('method') not in METHODS or type(q.get('human_reviewed')) is not bool or not isinstance(q.get('limitations'),list): raise ValueError('invalid reliability')
    e.setdefault('permission',{'state':'local_only','hold':False,'basis':'No GitHub publication grant recorded'})
    p=e['permission']
    if not isinstance(p,dict) or p.get('state') not in PERMISSIONS or type(p.get('hold')) is not bool or not isinstance(p.get('basis'),str): raise ValueError('invalid permission')
    e.setdefault('provenance',[])
    if not isinstance(e['provenance'],list): raise ValueError('provenance must be list')
    e.setdefault('refs',[])
    if not isinstance(e['refs'],list): raise ValueError('refs must be list')
    for ref in e['refs']: check_id(ref)
    if e['kind']=='segment':
        for k in ('start','end'):
            v=e.get(k)
            if type(v) not in (int,float) or not math.isfinite(v): raise ValueError('segment seconds must be finite')
        if e['start']<0 or e['end']<=e['start']: raise ValueError('invalid segment range')
        if e.get('time_basis')!='source_zero': raise ValueError('segment must use source_zero seconds')
        check_id(e.get('source_id'))
        if e['source_id'] not in e['refs']: e['refs'].append(e['source_id'])
    if e['kind']=='alignment':
        a=e.get('alignment')
        if not isinstance(a,dict) or a.get('scope')!='local_interval' or a.get('model')!='constant_offset':raise ValueError('alignment must declare a bounded local interval and supported model')
        for k in ('source_id','target_source_id'):check_id(a.get(k))
        for k in ('source_start','source_end','offset_seconds'):
            if type(a.get(k)) not in (int,float) or not math.isfinite(a[k]):raise ValueError('alignment values must be finite')
        if a['source_start']<0 or a['source_end']<=a['source_start'] or a['source_start']+a['offset_seconds']<0:raise ValueError('invalid local alignment range')
        u=a.get('uncertainty_seconds')
        if u is not None and (type(u) not in (int,float) or not math.isfinite(u) or u<0):raise ValueError('invalid alignment uncertainty')
        if not a.get('basis') or not a.get('uncertainty_note') or a.get('time_basis')!='source_zero' or a.get('extrapolation_allowed') is not False:raise ValueError('alignment requires evidence, uncertainty note, source-zero basis and no extrapolation')
        for k in ('source_id','target_source_id'):
            if a[k] not in e['refs']:e['refs'].append(a[k])
    if e['kind']=='event' and 'event_time' in e:
        t=e['event_time']
        if not isinstance(t,dict) or t.get('precision') not in {'day','month','year','unknown'} or 'source' not in t: raise ValueError('event time precision/source required')
        value=t.get('value')
        try:
            if t['precision']=='day': datetime.strptime(value,'%Y-%m-%d')
            elif t['precision']=='month': datetime.strptime(value,'%Y-%m')
            elif t['precision']=='year': datetime.strptime(value,'%Y')
            elif value is not None: raise ValueError('unknown event time must be null')
        except (ValueError,TypeError): raise ValueError('invalid event date')
    canonical(e)
    return e

def put_entity(db,record,reason,batch_id=None):
    e=normalized(record); sha=digest(e)
    old=db.execute('SELECT * FROM entity WHERE id=?',(e['id'],)).fetchone()
    if old and old['digest']==sha:return 'unchanged'
    if old and old['kind']!=e['kind']:raise ValueError('stable ID kind cannot change')
    version=old['version']+1 if old else 1; at=now(); body=canonical(e)
    db.execute('INSERT INTO entity VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,body=excluded.body,digest=excluded.digest,version=excluded.version,updated_at=excluded.updated_at',(e['id'],e['kind'],e['title'],body,sha,version,at))
    db.execute('INSERT INTO revision VALUES(?,?,?,?,?,?,?)',(e['id'],version,sha,body,at,reason,batch_id))
    return 'updated' if old else 'inserted'

def put_relation(db,relation,reason):
    r=dict(relation)
    for k in ('id','subject','object'):check_id(r.get(k))
    if r.get('predicate') not in PREDICATES:raise ValueError('unsupported relation predicate')
    r.setdefault('epistemic','unknown'); r.setdefault('provenance',[])
    sha=digest(r); old=db.execute('SELECT * FROM relation WHERE id=?',(r['id'],)).fetchone()
    if old and old['digest']==sha:return 'unchanged'
    version=old['version']+1 if old else 1; at=now(); body=canonical(r)
    db.execute('INSERT INTO relation VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET subject=excluded.subject,predicate=excluded.predicate,object=excluded.object,body=excluded.body,digest=excluded.digest,version=excluded.version,updated_at=excluded.updated_at',(r['id'],r['subject'],r['predicate'],r['object'],body,sha,version,at))
    db.execute('INSERT INTO relation_revision VALUES(?,?,?,?,?,?)',(r['id'],version,sha,body,at,reason))
    return 'updated' if old else 'inserted'

def entities(db):return [json.loads(r[0]) for r in db.execute('SELECT body FROM entity ORDER BY id')]
def get_entity(db,id):
    row=db.execute('SELECT body,digest,version FROM entity WHERE id=?',(id,)).fetchone()
    if not row: raise ValueError('entity not found')
    return {'record':json.loads(row[0]),'digest':row[1],'version':row[2]}

def validate(db, check_files=False):
    errors=[]; warnings=[]; rows={e['id']:e for e in entities(db)}
    integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
    if integrity!='ok':errors.append({'code':'sqlite_integrity','detail':integrity})
    for r in db.execute('PRAGMA foreign_key_check'):errors.append({'code':'foreign_key','detail':list(r)})
    for e in rows.values():
        try:normalized(e)
        except ValueError as ex:errors.append({'code':'record_schema','id':e['id'],'detail':str(ex)})
        for ref in e['refs']:
            if ref not in rows:errors.append({'code':'broken_reference','id':e['id'],'target':ref})
        if e['kind']=='segment':
            src=rows.get(e['source_id'],{})
            if src.get('kind') not in {'asset','source'}:errors.append({'code':'invalid_segment_source','id':e['id']})
            duration=src.get('metadata',{}).get('duration_seconds')
            if isinstance(duration,(int,float)) and e['end']>duration+0.1:errors.append({'code':'time_out_of_bounds','id':e['id']})
            if not duration:warnings.append({'code':'duration_unknown','id':e['id']})
        if e['kind']=='claim' and not e['refs'] and not db.execute("SELECT 1 FROM relation WHERE subject=? AND predicate='supports'",(e['id'],)).fetchone():warnings.append({'code':'claim_without_source','id':e['id']})
        if e['kind']=='person' and e.get('metadata',{}).get('identity_status')!='confirmed':warnings.append({'code':'identity_unresolved','id':e['id']})
        if e['kind']=='alignment':
            a=e['alignment']
            for key in ('source_id','target_source_id'):
                source=rows.get(a[key],{})
                if source.get('kind') not in {'asset','source'}:errors.append({'code':'invalid_alignment_source','id':e['id']})
                end=a['source_end']+(a['offset_seconds'] if key=='target_source_id' else 0)
                duration=source.get('metadata',{}).get('duration_seconds')
                if isinstance(duration,(int,float)) and end>duration+0.1:errors.append({'code':'alignment_out_of_bounds','id':e['id']})
        if e['kind']=='casting':
            kinds={rows[r]['kind'] for r in e['refs'] if r in rows}
            if not {'person','role','project'}<=kinds:errors.append({'code':'casting_incomplete','id':e['id']})
        if check_files and e.get('metadata',{}).get('local_path') and not Path(e['metadata']['local_path']).is_file():warnings.append({'code':'source_unavailable','id':e['id']})
    for r in db.execute('SELECT id,body,digest FROM entity'):
        if digest(json.loads(r['body']))!=r['digest']:errors.append({'code':'entity_digest','id':r['id']})
    for table,col in [('revision','entity_id'),('relation_revision','relation_id')]:
        for r in db.execute('SELECT * FROM '+table):
            if digest(json.loads(r['body']))!=r['digest']:errors.append({'code':'revision_digest','id':r[col]})
    for table,idcol,history in [('entity','entity_id','revision'),('relation','relation_id','relation_revision')]:
        for r in db.execute('SELECT * FROM '+table):
            if digest(json.loads(r['body']))!=r['digest']:errors.append({'code':'current_digest','id':r['id']})
            latest=db.execute('SELECT version,digest,body FROM '+history+' WHERE '+idcol+'=? ORDER BY version DESC LIMIT 1',(r['id'],)).fetchone()
            if not latest or tuple(latest)!=(r['version'],r['digest'],r['body']):errors.append({'code':'revision_head_mismatch','id':r['id']})
            count=db.execute('SELECT count(*) FROM '+history+' WHERE '+idcol+'=?',(r['id'],)).fetchone()[0]
            if count!=r['version']:errors.append({'code':'revision_gap','id':r['id']})
    previous=''
    for r in db.execute('SELECT * FROM audit ORDER BY seq'):
        if r['previous_hash']!=previous or r['hash']!=digest([previous,r['at'],r['action'],r['detail']]):errors.append({'code':'audit_chain','seq':r['seq']})
        previous=r['hash']
    from .review import validate_review_layer
    errors.extend(validate_review_layer(db))
    from .workflow import validate_workflow
    errors.extend(validate_workflow(db))
    from .identity import validate_identity_layer
    errors.extend(validate_identity_layer(db))
    from .interview_map import validate_interview_maps
    errors.extend(validate_interview_maps(db))
    from .locators import validate_locators
    errors.extend(validate_locators(db))
    return {'ok':not errors,'errors':errors,'warnings':warnings,'counts':{k:sum(e['kind']==k for e in rows.values()) for k in sorted(KINDS)},'audit_head':previous}

def search(db,query,limit=100,kind=''):
    # Python casefold handles Chinese single characters and literal %/_ safely.
    q=query.casefold(); out=[];confirmed_people=set()
    if q and kind in {'','person'} and db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_record'").fetchone():
        for r in db.execute("SELECT person_entity_id,body FROM identity_record WHERE kind='confirmation'"):
            if q in {v.casefold() for v in json.loads(r['body'])['record']['confirmed_variants']}:confirmed_people.add(r['person_entity_id'])
    for row in db.execute("SELECT id,kind,title,body,version FROM entity WHERE (?='' OR kind=?) ORDER BY kind,title",(kind,kind)):
        e=json.loads(row['body'])
        if e['id'] in confirmed_people or q in (e['title']+'\n'+e['text']+'\n'+e['id']).casefold():
            out.append({'id':e['id'],'kind':e['kind'],'title':e['title'],'version':row['version'],'method':e['reliability']['method'],'human_reviewed':e['reliability']['human_reviewed']})
            if len(out)>=limit:break
    return out

def timeline(db):
    result=[e for e in entities(db) if e['kind']=='event']
    return sorted(result,key=lambda e:(e.get('event_time',{}).get('value') is None,e.get('event_time',{}).get('value') or '',e['id']))

def graph(db,focus=None,limit=250):
    nodes={e['id']:e for e in entities(db)}
    edges=[json.loads(r[0]) for r in db.execute('SELECT body FROM relation ORDER BY id')]
    for e in nodes.values():
        meta=e.get('metadata',{});links=meta.get('representation_links',{})
        navigation={links.get('graph_node_id'),links.get('original_claim_id')}|set(meta.get('explicit_role_references',{}).get('resolved_ids',[]))
        for ref in e['refs']:
            edge={'id':'ref:'+digest([e['id'],ref])[:20],'subject':e['id'],'predicate':'source' if ref==e.get('source_id') else 'related_to','object':ref}
            if ref in navigation:edge.update({'compatibility_navigation':True,'counts_as_new_evidence':False,'basis':'explicit_source_ID_representation_link'})
            edges.append(edge)
    chosen=set(nodes) if not focus else {focus}|{r['object'] for r in edges if r['subject']==focus}|{r['subject'] for r in edges if r['object']==focus}
    ordered=sorted(chosen,key=lambda id:(id!=focus,id))[:limit]; chosen=set(ordered)
    return {'nodes':[{'id':id,'title':nodes[id]['title'],'kind':nodes[id]['kind']} for id in ordered if id in nodes], 'edges':[e for e in edges if e['subject'] in chosen and e['object'] in chosen], 'total_nodes':len(nodes),'truncated':len(ordered)<len(nodes)}

def duplicate_candidates(db):
    groups={}
    for e in entities(db):
        sha=e.get('metadata',{}).get('content_sha256')
        if sha:groups.setdefault(sha,[]).append(e['id'])
    return [{'sha256':sha,'ids':ids,'meaning':'same recorded content hash; identity/version/event not automatically merged'} for sha,ids in groups.items() if len(ids)>1]

def enqueue(db,kind,entity_id,detail,priority=5):
    id=stable_id('job',kind,entity_id or detail)
    db.execute("INSERT INTO job VALUES(?,?,?,'pending',?,?,?) ON CONFLICT(id) DO NOTHING",(id,entity_id,kind,priority,detail,now()))
    return id

def set_job_state(db,id,state):
    transitions={'pending':{'running','blocked','cancelled'},'running':{'done','blocked','pending','cancelled'},'blocked':{'pending','cancelled'},'done':set(),'cancelled':set()}
    row=db.execute('SELECT state FROM job WHERE id=?',(id,)).fetchone()
    if not row or state not in transitions[row[0]]:raise ValueError('invalid job state transition')
    db.execute('UPDATE job SET state=?,updated_at=? WHERE id=?',(state,now(),id));audit(db,'job_state',{'id':id,'state':state})


def map_local_time(record,seconds):
    e=normalized(record)
    if e['kind']!='alignment':raise ValueError('alignment record required')
    a=e['alignment']
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not a['source_start']<=seconds<=a['source_end']:raise ValueError('time outside verified local alignment interval; extrapolation prohibited')
    return {'target_seconds':seconds+a['offset_seconds'],'target_source_id':a['target_source_id'],'uncertainty_seconds':a['uncertainty_seconds'],'uncertainty_note':a['uncertainty_note'],'basis':a['basis'],'scope':'local_interval','extrapolation_allowed':False,'not_semantic_confirmation':True}
