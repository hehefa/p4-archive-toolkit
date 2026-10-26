"""Private, append-only identity/participation overlays; never rewrite source identities."""
from contextlib import nullcontext
import hashlib
import io
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from .core import canonical,digest,file_hash,stable_id,now,audit,entities
from .review import _member_payloads,bounded_read,anchor_parts,excerpt_source

MEMBERS={
 'card':('126人物身份证据总表.json','person_id'),
 'evidence':('人物项目事件覆盖_逐证据.json','evidence_id'),
 'alias':('明确别名与文件前缀_来源域.json','alias_id'),
 'bridge':('四候选人物到151的限定连接.json','bridge_id'),
 'position':('两个人物新增文稿参与位置.json','position_id'),
 'unmapped':('未映射称谓_稳定事件位置.json','anonymous_id'),
 'question':('用户只需先确认这三项.json','id'),
 'source':('原始来源注册表.json','source_id')}

def identity_id(kind,key):return stable_id('identity','p4-private-identity/'+kind,key)
def review_id(key):return stable_id('review','p4-private-people-review',key)
def link(kind,key):return {'kind':kind,'id':identity_id(kind,key),'target':'identity'}
def review_link(key):return {'kind':'review','id':review_id(key),'target':'review'}

def direct_paragraphs(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if z.getinfo('word/document.xml').file_size>32*1024*1024:raise ValueError('oversized identity source XML')
        xml=ET.fromstring(z.read('word/document.xml'))
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    body=xml.find('w:body',ns)
    if body is None:raise ValueError('missing Word body')
    result=[]
    for index,p in enumerate(body.findall('w:p',ns),1):
        text=''.join(t.text or '' for t in p.findall('.//w:t',ns))
        if text.strip():result.append({'location':'P'+str(len(result)+1),'xml_body_p':index,'text':text})
    return result

def source_excerpt(path,raw,anchor):
    if Path(path).suffix.lower()!='.docx':return excerpt_source(path,raw,anchor)
    lines=direct_paragraphs(raw);wanted=set()
    for kind,start,end in anchor_parts(anchor):
        if kind!='P' or end>len(lines):raise ValueError('identity anchor outside source')
        wanted.update(range(start,end+1))
    return {'anchor':anchor,'numbering':'direct body nonempty Word paragraphs, one-based; XML body paragraph index retained','lines':[lines[i-1] for i in sorted(wanted)],'source_sha256':hashlib.sha256(raw).hexdigest()}

def _persons(db):
    return {e['metadata']['upstream_record']['id']:e for e in entities(db) if e['kind']=='person' and e.get('metadata',{}).get('upstream_section')=='nodes'}

def _persist(db,root,manifest_sha,payload,manifest_raw,local_dir,records,receipt,commit=True):
    """Only append new IDs. A changed record requires a separately identified correction."""
    package_id=stable_id('identity-package','p4-private-identity',manifest_sha)
    known={r[0] for r in db.execute('SELECT id FROM identity_record')}|{r['id'] for r in records}
    reviews={r[0] for r in db.execute('SELECT id FROM review_record')}
    if len({r['id'] for r in records})!=len(records):raise ValueError('duplicate identity IDs')
    for r in records:
        for ref in r['body']['links']:
            if ref['id'] not in (known if ref['target']=='identity' else reviews):raise ValueError('unresolved identity navigation')
        old=db.execute('SELECT digest,package_id FROM identity_record WHERE id=?',(r['id'],)).fetchone()
        if old and (old['digest']!=digest(r['body']) or old['package_id']!=package_id):raise ValueError('identity ID collision or changed immutable overlay')
    prior=db.execute('SELECT * FROM identity_package WHERE id=?',(package_id,)).fetchone()
    if prior:
        if len(records)!=db.execute('SELECT count(*) FROM identity_record WHERE package_id=?',(package_id,)).fetchone()[0] or validate_identity_layer(db):raise ValueError('identity replay drift')
        _member_payloads(Path(local_dir)/prior['snapshot_path'],manifest_sha)
        return {'status':'already_imported','records':len(records),'package_id':package_id}
    retained=Path(local_dir)/'research_snapshots'/('identity-'+manifest_sha)
    retained.mkdir(parents=True,exist_ok=True)
    for name,raw in {**payload,'MANIFEST.json':manifest_raw}.items():
        p=retained/name;p.parent.mkdir(parents=True,exist_ok=True)
        if p.exists() and p.read_bytes()!=raw:raise ValueError('identity snapshot collision')
        if not p.exists():p.write_bytes(raw)
    receipt={**receipt,'visibility':'local_private','record_ids':[r['id'] for r in records],'source_manifest_sha256':manifest_sha}
    with (db if commit else nullcontext()):
        db.execute('INSERT INTO identity_package VALUES(?,?,?,?,?,?)',(package_id,manifest_sha,canonical(receipt),digest(receipt),str(retained.relative_to(Path(local_dir))),now()))
        for r in records:
            body=canonical(r['body']);sha=digest(r['body']);at=now()
            db.execute('INSERT INTO identity_record VALUES(?,?,?,?,?,?,?,?,?)',(r['id'],r['upstream_id'],r['kind'],r['person_entity_id'],package_id,body,sha,1,at))
            db.execute('INSERT INTO identity_revision VALUES(?,?,?,?,?,?)',(r['id'],1,package_id,body,sha,at))
        audit(db,'import_private_identity',{'package_id':package_id,'records':len(records),'core_changed':False})
    return {'status':'imported','records':len(records),'package_id':package_id,'core_changed':False}

def _record(kind,key,raw,people,person=None,links=(),**extra):
    if person and person not in people:raise ValueError('unknown explicit person ID')
    return {'id':identity_id(kind,key),'upstream_id':key,'kind':kind,'person_entity_id':people[person]['id'] if person else None,
            'body':{'record':raw,'links':list(links),'visibility':'local_private','biometric_identification':False,**extra}}

def import_identity(db,folder,manifest_sha,local_dir):
    from .graph_adapter import current_graph_state
    root=Path(folder).resolve();payload,manifest_raw=_member_payloads(root,manifest_sha)
    delta=json.loads(payload['delta_156_身份参与侧表.json'])
    if delta.get('schema')!='p4.private.identity-participation/156' or delta.get('visibility')!='local_private' or any(delta.get(k) for k in ('identity_merges','new_persons','new_whole_events')):raise ValueError('private non-merging identity package required')
    state=current_graph_state(db)
    if not state or state['graph_sha256']!=delta['base89_sha256'] or file_hash(delta['base89'])!=delta['base89_sha256']:raise ValueError('identity base graph mismatch')
    base=json.loads(bounded_read(delta['base89']));review_raw=bounded_read(delta['review151'])
    if hashlib.sha256(review_raw).hexdigest()!=delta['review151_sha256']:raise ValueError('identity review base mismatch')
    review_data=json.loads(review_raw);reviews={r['upstream_id']:json.loads(r['body'])['record'] for r in db.execute('SELECT * FROM review_record')}
    baseline_reviews={r['id']:r for r in review_data['records']}
    if {k:reviews.get(k) for k in baseline_reviews}!=baseline_reviews or set(baseline_reviews)!=set(delta['preserved_review_ids']):raise ValueError('identity review head drift')
    people=_persons(db);tables={k:json.loads(payload[name]) for k,(name,_) in MEMBERS.items()}
    cards={r['person_id']:r for r in tables['card']}
    if set(cards)!=set(delta['preserved_person_ids']) or set(cards)!={r['id'] for r in base['nodes'] if r.get('type')=='人物'} or not set(cards)<=set(people):raise ValueError('identity person baseline mismatch')
    for key,kind in [('added_scoped_aliases','alias'),('review_link_overlays','bridge'),('new_document_positions','position')]:
        if delta[key]!=tables[kind]:raise ValueError('identity delta member disagreement')
    roles={r['id']:r for r in base['role_records']}
    if {r['old_role_id'] for r in tables['evidence']}!=set(roles):raise ValueError('incomplete inherited role coverage')
    for row in tables['evidence']:
        old=roles.get(row['old_role_id'])
        fields={'person_id':'person_id','context_id':'context_id','context':'context','role':'role','old_status':'status','date':'date','date_kind':'date_kind','source_id':'source','anchor':'anchor','old_excerpt':'excerpt','uncertainty':'uncertainty'}
        if not old or any(row.get(k)!=old.get(v) for k,v in fields.items()):raise ValueError('inherited role evidence changed')
    cache={};declared={r['path']:r for r in json.loads(payload['实际定向读取账.json'])}
    for path,row in declared.items():
        raw=bounded_read(path)
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('identity directed source hash mismatch')
        cache[path]=raw
    def excerpt(path,anchor):
        if path not in cache:cache[path]=bounded_read(path)
        return source_excerpt(path,cache[path],anchor)
    records=[]
    for kind,rows in tables.items():
        idkey=MEMBERS[kind][1]
        for row in rows:
            key=row[idkey];person=row.get('person_id') or row.get('person_card_candidate');refs=[];extra={}
            if kind=='card':
                for field,target in [('evidence_ids','evidence'),('source_ids','source'),('explicit_alias_evidence','alias'),('qualified_existing_review_links','bridge'),('new_source_participation_positions','position')]:refs.extend(link(target,x) for x in row[field])
                if any(r['person_id']!=person for r in tables['evidence'] if r['evidence_id'] in row['evidence_ids']):raise ValueError('card evidence person mismatch')
            elif kind=='evidence':
                refs=[link('card',person),link('source',row['source_id'])]
                extra['source_evidence']={'anchor':row['anchor'],'lines':[{'location':row['anchor'],'text':row['old_excerpt']}],'numbering':'Inherited legacy anchor, not reinterpreted as current Word paragraph','verification':'exact inherited role record; no new source reading'}
            elif kind=='alias':
                if Path(row['scope']).is_absolute():
                    proof=excerpt(row['scope'],row['anchor'])
                    if not any(row['excerpt'] in x['text'] and ('xml_body_p' not in row or x.get('xml_body_p')==row['xml_body_p']) for x in proof['lines']):raise ValueError('alias source excerpt mismatch')
                    proof['hash_status']='declared_verified' if row['scope'] in declared else 'observed_at_import';extra['source_evidence']=proof
                else:extra['source_evidence']={'verification':'upstream supplied user confirmation; prefix scope only','lines':[]}
            elif kind=='bridge':
                if row['changes_existing_records'] or row['biometric_use']:raise ValueError('bridge scope invalid')
                refs=[review_link(x) for x in row['existing_review_ids']]
                if any(reviews[x]['source'] not in {r['path'] for r in row['references']} for x in row['existing_review_ids']):raise ValueError('bridge review source mismatch')
                extra['source_evidence']=[{'path':r['path'],**excerpt(r['path'],r['anchor'])} for r in row['references']]
            elif kind=='position':
                if row['whole_event_newly_discovered']:raise ValueError('position cannot create event')
                proof=excerpt(row['source'],'、'.join('P'+str(a['P']) for a in row['anchors']))
                if any(not any(x['location']=='P'+str(a['P']) and x['xml_body_p']==a['xml_body_p'] and x['text']==a['text'] for x in proof['lines']) for a in row['anchors']):raise ValueError('participation source excerpt mismatch')
                extra['source_evidence']=proof
            elif kind=='unmapped':
                if row['is_new_person']:raise ValueError('unmapped position cannot create person')
                refs=[review_link(x) for x in row['review_records']]
            elif kind=='question':extra['state']='unconfirmed'
            elif kind=='source':
                path=row.get('path');p=Path(path) if path else None
                extra['source_observation']={'exists_at_import':p.exists() if p else None,'bytes':p.stat().st_size if p and p.exists() else None,'content_read':path in cache,'hash_status':'declared_verified' if path in declared else 'not_hashed','sha256':declared[path]['sha256'] if path in declared else None}
            if person and kind!='card':refs.append(link('card',person))
            records.append(_record(kind,key,row,people,person,refs,**extra))
    return _persist(db,root,manifest_sha,payload,manifest_raw,local_dir,records,{'schema':delta['schema'],'delta_member':'delta_156_身份参与侧表.json','base_graph_sha256':delta['base89_sha256'],'review_data_sha256':delta['review151_sha256'],'source_proofs':{path:{'sha256':hashlib.sha256(raw).hexdigest(),'hash_status':'declared_verified' if path in declared else 'observed_at_import'} for path,raw in cache.items()}})

def import_confirmation(db,folder,manifest_sha,local_dir):
    from .graph_adapter import current_graph_state
    root=Path(folder).resolve();payload,manifest_raw=_member_payloads(root,manifest_sha);d=json.loads(payload['delta_157_研究确认补证.json'])
    if d.get('schema')!='p4.private.identity-confirmation/157' or d.get('visibility')!='local_private' or d.get('biometric_identification') is not False or any(d.get(k) for k in ('new_people','new_interactions','record_replacements')):raise ValueError('invalid private confirmation overlay')
    pre=d['preconditions']
    if current_graph_state(db)['graph_sha256']!=pre['base89_sha256'] or not db.execute('SELECT 1 FROM identity_package WHERE manifest_sha256=?',(pre['156_manifest_sha256'],)).fetchone() or not db.execute('SELECT 1 FROM review_package WHERE manifest_sha256=?',(pre['151_manifest_sha256'],)).fetchone():raise ValueError('confirmation baseline missing')
    evidence=d['evidence'];parent=db.execute('SELECT snapshot_path FROM identity_package WHERE manifest_sha256=?',(pre['156_manifest_sha256'],)).fetchone()
    question_bytes=bounded_read(Path(local_dir).resolve()/parent[0]/MEMBERS['question'][0])
    if hashlib.sha256(question_bytes).hexdigest()!=evidence['questions_sha256']:raise ValueError('confirmation questions hash mismatch')
    groups=d['confirmation_groups'];people=_persons(db);records=[];variants=set();targets=set();bygroup={}
    if not evidence.get('messages') or any(not r.get('message_id') or not r.get('text') for r in evidence['messages']):raise ValueError('confirmation user evidence missing')
    if json.loads(payload['用户确认来源.json'])!=evidence:raise ValueError('confirmation evidence disagreement')
    for member,key in [('三组各自同人_稳定ID映射.json','confirmation_groups'),('十二条原稿标签补通.json','alias_resolutions'),('四条既有事件记录人名补通.json','existing_review_person_links'),('旧待映射位置可逆指向.json','position_redirects')]:
        if json.loads(payload[member])!=d[key]:raise ValueError('confirmation member disagreement')
    def old(kind,key):
        row=db.execute('SELECT body FROM identity_record WHERE id=?',(identity_id(kind,key),)).fetchone()
        if not row:raise ValueError('confirmation reference missing')
        return json.loads(row[0])['record']
    for g in groups:
        person=g['canonical_person_id'];names=set(g['confirmed_variants'])
        if person in targets or variants&names or len(names)!=len(g['confirmed_variants']) or not g['original_spelling_preserved'] or g['user_evidence']!=evidence['source_id']:raise ValueError('confirmation groups overlap')
        if old('card',person)['name']!=g['canonical_name']:raise ValueError('confirmation canonical card mismatch')
        old('question',g['question_id']);variants|=names;targets.add(person);bygroup[g['confirmation_id']]=g
        records.append(_record('confirmation',g['confirmation_id'],g,people,person,[link('question',g['question_id']),link('card',person)],state='user_confirmed',user_evidence=evidence,date_basis='source confirmation date retained as provided; import time stored separately'))
    def group(row):
        g=bygroup[row['confirmation_id']]
        if row['canonical_person_id']!=g['canonical_person_id'] or row['canonical_name']!=g['canonical_name']:raise ValueError('confirmation target disagreement')
        return g
    for r in d['alias_resolutions']:
        g=group(r);a=old('alias',r['prior_alias_id'])
        if (a['alias'],a['document_name'],a['scope'],a['anchor'],a['excerpt'])!=(r['original_alias'],r['original_document_name'],r['source_path'],r['anchor'],r['excerpt']) or a['document_name'] not in g['confirmed_variants']:raise ValueError('alias resolution source mismatch')
        records.append(_record('resolution',r['resolution_id'],r,people,g['canonical_person_id'],[link('alias',r['prior_alias_id']),link('confirmation',g['confirmation_id'])]))
    for r in d['existing_review_person_links']:
        g=group(r);head=db.execute('SELECT body FROM review_record WHERE id=?',(review_id(r['existing_review_id']),)).fetchone()
        if not head:raise ValueError('confirmation review missing')
        oldrow=json.loads(head[0])['record']
        if any(oldrow.get(k)!=r[v] for k,v in [('source','source'),('anchor','anchor'),('claim','original_claim'),('limit','original_limit')]) or r['old_label'] not in oldrow['people']:raise ValueError('confirmation review body mismatch')
        records.append(_record('review-link',r['link_id'],r,people,g['canonical_person_id'],[review_link(r['existing_review_id']),link('confirmation',g['confirmation_id'])]))
    for r in d['position_redirects']:
        g=group(r);a=old('unmapped',r['prior_anonymous_position_id'])
        if (a['label'],a['source'],a['event'],a['review_records'])!=(r['original_label'],r['source'],r['event'],r['review_records']) or not r['keep_original_id_as_redirect']:raise ValueError('confirmation position mismatch')
        records.append(_record('redirect',r['prior_anonymous_position_id'],r,people,g['canonical_person_id'],[link('unmapped',r['prior_anonymous_position_id']),link('confirmation',g['confirmation_id'])]))
    return _persist(db,root,manifest_sha,payload,manifest_raw,local_dir,records,{'schema':d['schema'],'idempotency_key':d['idempotency_key'],'preconditions':pre,'user_message_ids':[r['message_id'] for r in evidence['messages']]})

def validate_identity_layer(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_record'").fetchone():return []
    errors=[];rows={r['id']:r for r in db.execute('SELECT * FROM identity_record')};reviews={r[0] for r in db.execute('SELECT id FROM review_record')}
    for p in db.execute('SELECT * FROM identity_package'):
        b=json.loads(p['body'])
        if digest(b)!=p['digest'] or b.get('visibility')!='local_private' or set(b['record_ids'])!={r['id'] for r in rows.values() if r['package_id']==p['id']}:errors.append({'code':'identity_package_integrity','id':p['id']})
    for r in rows.values():
        b=json.loads(r['body']);history=db.execute('SELECT version,digest,body FROM identity_revision WHERE record_id=? ORDER BY version',(r['id'],)).fetchall()
        if digest(b)!=r['digest'] or b.get('visibility')!='local_private' or b.get('biometric_identification') is not False or len(history)!=r['version'] or not history or tuple(history[-1])!=(r['version'],r['digest'],r['body']):errors.append({'code':'identity_record_integrity','id':r['id']})
        for ref in b.get('links',[]):
            if ref['target'] not in {'identity','review'} or ref['id'] not in (rows if ref['target']=='identity' else reviews):errors.append({'code':'identity_broken_reference','id':r['id']})
    for r in db.execute('SELECT * FROM identity_revision'):
        if digest(json.loads(r['body']))!=r['digest']:errors.append({'code':'identity_revision_integrity','id':r['record_id']})
    return errors

def identity_detail(db,id):
    row=db.execute('SELECT * FROM identity_record WHERE id=? OR upstream_id=? ORDER BY kind',(id,id)).fetchall()
    if len(row)!=1:raise ValueError('identity record missing or ambiguous; use stable ID')
    r=dict(row[0]);r['body']=json.loads(r['body']);r['related']=[]
    if r['kind']=='card':
        related=db.execute('SELECT id,kind,body FROM identity_record WHERE person_entity_id=? AND kind!=?',(r['person_entity_id'],'card')).fetchall()
    else:related=db.execute('SELECT id,kind,body FROM identity_record WHERE kind IN (?,?,?)',('confirmation','redirect','resolution')).fetchall()
    for item in related:
        b=json.loads(item['body'])
        if r['kind']=='card' or any(x['id']==r['id'] for x in b['links']):r['related'].append({'id':item['id'],'kind':item['kind'],'body':b})
    r['navigation']=[]
    for ref in r['body']['links']:
        table='identity_record' if ref['target']=='identity' else 'review_record'
        target=db.execute('SELECT upstream_id,body FROM '+table+' WHERE id=?',(ref['id'],)).fetchone()
        if target:
            raw=json.loads(target['body'])['record'];label=raw.get('name') or raw.get('document_name') or raw.get('canonical_name') or raw.get('context') or raw.get('source_id') or raw.get('label') or raw.get('event') or target['upstream_id']
            if ref['kind']=='alias':label=raw['alias']+' = '+raw['document_name']
            r['navigation'].append({**ref,'label':label,'upstream_id':target['upstream_id']})
    r['history']=[dict(h) for h in db.execute('SELECT version,digest,changed_at FROM identity_revision WHERE record_id=?',(r['id'],))]
    return r

def identity_search(db,q='',priority=False,entity='',context=''):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_record'").fetchone():return {'count':0,'total':0,'priority_total':0,'records':[],'contexts':[]}
    rows=[dict(r) for r in db.execute('SELECT * FROM identity_record ORDER BY kind,upstream_id')]
    for r in rows:r['body']=json.loads(r['body'])
    cards=[r for r in rows if r['kind']=='card'];out=[];contexts=set()
    for card in cards:
        raw=card['body']['record'];linked=[r for r in rows if r['person_entity_id']==card['person_entity_id']];terms=[raw['name'],raw['person_id']];project=[];confirmed=[]
        for r in linked:
            a=r['body']['record']
            if r['kind']=='confirmation':terms.extend(a['confirmed_variants']);confirmed.append(a)
            if r['kind']=='evidence':project.append(a['context']);terms.extend([a['context'],a.get('source_path') or '',a.get('role') or ''])
            if r['kind']=='alias':terms.extend([a['alias'],a['scope']])
            if r['kind']=='archive-alias':terms.extend([a['alias'],a['source_path']])
            if r['kind']=='resolution':terms.extend([a['original_alias'],a['source_path']])
        contexts.update(project)
        if priority and not raw['priority_no_direct_event'] or entity and entity!=card['person_entity_id'] or context and context not in project or q.casefold() not in '\n'.join(terms).casefold():continue
        out.append({'id':card['id'],'upstream_id':card['upstream_id'],'name':raw['name'],'person_entity_id':card['person_entity_id'],'priority':raw['priority_no_direct_event'],'evidence_count':len(raw['evidence_ids']),'confirmed_variants':[v for c in confirmed for v in c['confirmed_variants']]})
    return {'count':len(out),'total':len(cards),'priority_total':sum(r['body']['record']['priority_no_direct_event'] for r in cards),'records':out,'contexts':sorted(contexts)}
