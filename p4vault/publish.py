"""Revision-bound, destination-specific, fail-closed public projections."""
import json
import re
from pathlib import Path
from .core import *

PUBLIC_FIELDS={'title','text','epistemic','reliability','event_time','start','end','time_basis','source_id','refs','public_url','license'}
PATTERNS=[r'/Users/',r'/Volumes/',r'(?i)(?:access_token|refresh_token|private_key|BEGIN [A-Z ]*PRIVATE KEY)',r'\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b',r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',r'(?<![A-Za-z0-9])1[3-9]\d{9}(?![A-Za-z0-9])']

def scan_sensitive(value):
    text=canonical(value)
    return [str(i) for i,p in enumerate(PATTERNS) if re.search(p,text)]

def grant(db,review):
    if review.get('decision')!='approved':raise ValueError('explicit approved decision required')
    for k in ('destination','approved_by','evidence'):
        if not isinstance(review.get(k),str) or not review[k].strip():raise ValueError(k+' required')
    if review['destination'] not in {'github-index','public-site'}:raise ValueError('unsupported publication destination')
    if not review.get('records'):raise ValueError('review has no records')
    ids=[]
    with db:
        for r in review['records']:
            entity=get_entity(db,r['id']);e=entity['record']
            if entity['digest']!=r.get('digest'):raise ValueError('stale review revision')
            fields=r.get('fields',[])
            if not fields or len(fields)!=len(set(fields)) or not set(fields)<=PUBLIC_FIELDS:raise ValueError('public fields must be an explicit allowlist')
            if e['permission']['hold'] or e['permission']['state']!='public_approved':raise ValueError('entity is not cleared for publication')
            projected={'id':e['id'],'kind':e['kind'],**{k:e[k] for k in fields if k in e}}
            if scan_sensitive(projected):raise ValueError('sensitive field pattern in proposed public payload')
            gid=stable_id('grant',review['destination'],canonical([r['id'],r['digest'],sorted(fields),review['evidence']]))
            db.execute('INSERT OR IGNORE INTO grant_record VALUES(?,?,?,?,?,?,?,?,NULL)',(gid,r['id'],r['digest'],review['destination'],canonical(sorted(fields)),review['approved_by'],review['evidence'],now()))
            ids.append(gid)
        audit(db,'grant',{'ids':ids,'destination':review['destination'],'evidence':review['evidence']})
    return ids

def public_projection(db,destination):
    if destination not in {'github-index','public-site'}:raise ValueError('unsupported destination')
    validation=validate(db)
    if not validation['ok']:raise ValueError('database integrity failed; publication blocked')
    records=[];grants=[]
    for row in db.execute('SELECT * FROM grant_record WHERE destination=? AND revoked_at IS NULL ORDER BY created_at,id',(destination,)):
        obj=get_entity(db,row['entity_id']);e=obj['record']
        if row['digest']!=obj['digest']:raise ValueError('stale publication grant: '+e['id'])
        if e['permission']['hold'] or e['permission']['state']!='public_approved':raise ValueError('publication hold: '+e['id'])
        fields=json.loads(row['fields'])
        projected={'id':e['id'],'kind':e['kind'],**{k:e[k] for k in fields if k in e}}
        if scan_sensitive(projected):raise ValueError('sensitive field pattern: '+e['id'])
        if any(x['id']==e['id'] for x in records):raise ValueError('multiple active grants; revoke old grant first')
        records.append(projected);grants.append({'entity_id':e['id'],'revision_sha256':obj['digest'],'grant_id':row['id']})
    ids={e['id'] for e in records}
    allrows={e['id']:e for e in entities(db)}
    for e in records:
        refs=set(e.get('refs',[]))|({e['source_id']} if e.get('source_id') else set())
        if not refs<=ids:raise ValueError('public projection has dangling references: '+e['id'])
        # Holds propagate through source/derivation links, even when refs are not exported.
        if e['kind'] in {'claim','segment'} and not e.get('refs'):raise ValueError('public claims and segments require approved source references')
        adjacency={}
        for edge in db.execute("SELECT subject,object FROM relation WHERE predicate IN ('source','supports','derived_from','version_of')"):
            adjacency.setdefault(edge[0],[]).append(edge[1])
        todo=list(allrows[e['id']].get('refs',[]))+adjacency.get(e['id'],[]);seen=set()
        while todo:
            ref=todo.pop()
            if ref in seen:continue
            seen.add(ref);source=allrows.get(ref)
            if not source or source['permission']['hold'] or source['permission']['state']=='withdrawn':raise ValueError('source missing, held or withdrawn: '+e['id'])
            todo.extend(source.get('refs',[]));todo.extend(adjacency.get(ref,[]))
    return {'format':'p4-public-v1','destination':destination,'entities':records,'release_evidence':grants,'count':len(records)}

def revoke(db,id,reason):
    with db:
        cursor=db.execute('UPDATE grant_record SET revoked_at=? WHERE id=? AND revoked_at IS NULL',(now(),id))
        if cursor.rowcount!=1:raise ValueError('active grant not found')
        audit(db,'revoke',{'id':id,'reason':reason})

def write_public(db,destination,path):
    payload=public_projection(db,destination)
    if not payload['entities']:raise ValueError('no revision-specific publication grants; no output written')
    p=Path(path)
    if p.exists():raise ValueError('refuse overwrite; use a versioned release filename')
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(canonical(payload)+'\n')
    return {'count':payload['count'],'sha256':file_hash(p),'path':str(p)}
