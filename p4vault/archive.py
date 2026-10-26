"""Append-only private process-archive adapter. Source positions are not new people/events."""
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from .core import canonical,digest,file_hash,stable_id,now,audit,entities
from .review import _member_payloads,bounded_read,excerpt_source,validate_review_layer
from .identity import _persons,_record,_persist,identity_id,link,review_link,validate_identity_layer

TYPES=['creative_self_report','proposal','creative_self_report','creative_retrospective','documented_qa','noncausal_comparison']

def archive_excerpt(path,raw,anchor):
    match=re.fullmatch(r'XML body P([1-9]\d*)',anchor)
    if not match:return excerpt_source(path,raw,anchor)
    if Path(path).suffix.lower()!='.docx':raise ValueError('XML body anchor needs DOCX')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if z.getinfo('word/document.xml').file_size>32*1024*1024:raise ValueError('oversized archive XML')
        xml=ET.fromstring(z.read('word/document.xml'))
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'};rows=xml.findall('.//w:body/w:p',ns);n=int(match[1])
    if n>len(rows):raise ValueError('archive XML anchor outside body')
    text=''.join(t.text or '' for t in rows[n-1].findall('.//w:t',ns))
    return {'anchor':anchor,'numbering':'direct XML body paragraphs including empty paragraphs, one-based','lines':[{'location':anchor,'text':text}],'source_sha256':__import__('hashlib').sha256(raw).hexdigest()}

def _protected(db,folder,local,d):
    from .graph_adapter import current_graph_state
    if current_graph_state(db)['graph_sha256']!=d['base89_sha256']:raise ValueError('archive graph baseline mismatch')
    receipts=[]
    for name,sha in d['protected_base_manifests'].items():
        if Path(name).name!=name:raise ValueError('invalid protected package name')
        _member_payloads(folder.parent/name,sha)
        found=[]
        for kind in ('review','identity'):
            row=db.execute('SELECT * FROM '+kind+'_package WHERE manifest_sha256=?',(sha,)).fetchone()
            if row:found.append((kind,row))
        if not found:raise ValueError('archive protected baseline not imported')
        for kind,row in found:
            payload,_=_member_payloads(Path(local)/row['snapshot_path'],sha);receipt=json.loads(row['body'])
            if kind=='review':
                raw=json.loads(payload[receipt['data_member']])
                if 'records' in raw:records=raw['records'];original=receipt.get('schema')=='p4.private.adjacent-research/162' or receipt.get('schema','').startswith('p4.private.continuation/')
                elif 'added_records' in raw:records=raw['added_records'];original=True
                elif 'added_stage_records' in raw:records=raw['added_stage_records'];original=True
                elif 'stage_records' in raw:records=raw['stage_records'];original=True
                elif 'rows' in raw:records=raw['rows'];original=True
                else:raise ValueError('unsupported protected review schema')
                for r in records:
                    head=db.execute('SELECT body FROM review_record WHERE upstream_id=?',(r.get('id',r.get('claim_id')),)).fetchone()
                    current=json.loads(head[0])['record'] if head else None
                    if original and current:current=current.get('original_record')
                    if current!=r:raise ValueError('archive protected review drift')
            receipts.append({'kind':kind,'manifest_sha256':sha,'package_id':row['id']})
    if validate_review_layer(db) or validate_identity_layer(db):raise ValueError('private baseline integrity failed')
    return receipts

def import_archive(db,folder,manifest_sha,local_dir):
    root=Path(folder).resolve();payload,manifest_raw=_member_payloads(root,manifest_sha)
    d=json.loads(payload['delta_158_系统研究增量.json'])
    if d.get('schema')!='p4.private.archive-increment/158' or d.get('visibility')!='local_private' or any(d.get(k) for k in ('replaced_records','removed_records','global_identity_merges')):raise ValueError('append-only private archive required')
    protected=_protected(db,root,local_dir,d)
    if d['added_records']!=json.loads(payload['六项过程档案.json']) or d['source_registry']!=json.loads(payload['来源与阅读范围.json']) or d['source_scoped_people']!=json.loads(payload['人物署名位置与身份边界.json']):raise ValueError('archive package member disagreement')
    rows=d['added_records'];ids=[r['id'] for r in rows]
    if ids!=['NEW158-'+str(i).zfill(2) for i in range(1,7)]:raise ValueError('unsupported archive record layout')
    if len(d['source_registry'])!=3 or len({r['id'] for r in d['source_registry']})!=3:raise ValueError('archive sources must be explicit and unique')
    registry={r['id']:r for r in d['source_registry']};raws={}
    for id,row in registry.items():
        raw=bounded_read(row['path'])
        if __import__('hashlib').sha256(raw).hexdigest()!=row['sha256']:raise ValueError('archive source hash mismatch')
        raws[id]=raw
    primary,mirror,comparison=[r['id'] for r in d['source_registry']]
    anchors=json.loads(payload['精确原文锚点_私有.json']);verified={}
    for anchor in anchors:
        sid=anchor['source_id'];proof=archive_excerpt(registry[sid]['path'],raws[sid],anchor['anchor'])
        if [x['text'] for x in proof['lines']]!=[anchor['text']]:raise ValueError('archive exact excerpt mismatch')
        key=(sid,anchor['anchor'])
        if key in verified:raise ValueError('duplicate archive anchor')
        verified[key]=proof
    span=re.search(r'L(\d+)[–-](\d+)',registry[primary]['scope_read'])
    if not span:raise ValueError('primary reading range missing')
    lines=raws[primary].decode('utf-8-sig').splitlines();a,b=map(int,span.groups())
    if a<1 or b>len(lines):raise ValueError('primary reading range outside source')
    prose=[(i+1,text) for i,text in enumerate(lines) if a<=i+1<=b and len(text)>70 and '![' not in text and 'http' not in text]
    mirror_lines=raws[mirror].decode('utf-8-sig').splitlines();matched=[{'primary_line':i,'mirror_lines':[j+1 for j,y in enumerate(mirror_lines) if y==text],'text_sha256':digest(text)} for i,text in prose]
    declared=json.loads(payload['去重与排除记录.json'])['mirror_long_prose_identical']
    if not prose or len(prose)!=declared['total'] or sum(bool(r['mirror_lines']) for r in matched)!=declared['matched'] or declared['matched']!=declared['total']:raise ValueError('mirror shared prose claim failed')
    family_key=stable_id('family','p4-source-family',canonical(sorted([registry[primary]['sha256'],registry[mirror]['sha256']])))
    family={'family_id':family_key,'source_ids':[primary,mirror],'matched_long_paragraphs':len(prose),'matches':matched,'independent_evidence_families':1,'mirror_is_independent_witness':False,'scope':'verified shared long prose only; publication wrappers remain distinct'}
    people=_persons(db);positions={r['id']:r for r in d['source_scoped_people']}
    if len(positions)!=len(d['source_scoped_people']):raise ValueError('duplicate scoped person IDs')
    aliases=json.loads(payload[d['aliases_file']]);side=[]
    side.append(_record('source-family',family_key,family,people,links=[link('archive-source',primary),link('archive-source',mirror)]))
    for sid,row in registry.items():
        snippets=[{'path':row['path'],**v} for (source,_),v in verified.items() if source==sid]
        side.append(_record('archive-source',sid,row,people,links=([link('source-family',family_key)] if sid in {primary,mirror} else [])+([link('archive-person',id) for id in positions] if sid==primary else []),source_evidence=snippets))
    for id,row in positions.items():
        is_core=id in people
        if not is_core and not id.startswith('DOC158-'):raise ValueError('unknown archive person ID')
        if is_core and people[id]['title']!=row['name']:raise ValueError('archive core person name mismatch')
        refs=[review_link(r['id']) for r in rows if id in r['people_ids']]+[link('archive-source',primary)]
        if is_core:refs.append(link('card',id))
        # These are source-bound navigation positions, including unresolved same-name candidates.
        side.append(_record('archive-person',id,row,people,id if is_core else None,refs,global_person_created=False,candidate_identity_confirmed=False))
    for alias in aliases:
        sid=alias['source_id'];proof=verified[(sid,alias['anchor'])]
        if not any(x['text']==alias['explicit_text'] for x in proof['lines']):raise ValueError('scoped alias proof mismatch')
        person=alias.get('person_id');position=person or alias['source_position_id']
        if position not in positions:raise ValueError('alias scoped position missing')
        refs=[link('archive-person',position),link('archive-source',sid)]
        if person:
            confirmation=db.execute("SELECT body,person_entity_id FROM identity_record WHERE kind='confirmation' AND upstream_id=?",(alias['user_confirmation'],)).fetchone()
            if not confirmation or confirmation['person_entity_id']!=people[person]['id']:raise ValueError('alias prior confirmation mismatch')
            g=json.loads(confirmation['body'])['record']
            if g['canonical_name']!=alias['canonical'] or not any(x in alias['explicit_text'] for x in g['confirmed_variants']):raise ValueError('alias confirmed spelling absent')
            refs.append(link('confirmation',alias['user_confirmation']))
        key=stable_id('scoped-alias','p4-archive-alias',canonical([sid,alias['anchor'],alias['alias']]))
        record={**alias,'alias_id':key,'source_path':registry[sid]['path'],'global_alias':False}
        side.append(_record('archive-alias',key,record,people,person,refs,source_evidence=proof))
    prepared=[]
    for row,record_type in zip(rows,TYPES):
        if row['visibility']!='local_private' or row.get('biometric_identity') is not False or row.get('event_date') is not None or len(row['people_ids'])!=len(row['people_labels']) or any(id not in positions for id in row['people_ids']):raise ValueError('archive record identity/date boundary failed')
        if any(positions[id]['name']!=label for id,label in zip(row['people_ids'],row['people_labels'])):raise ValueError('archive person labels disagree')
        source_proofs=[]
        for anchor in row['anchors']:
            if len(row['source_ids'])==1:sid=row['source_ids'][0];location=anchor
            else:sid,location=anchor.split(' ',1)
            if sid not in row['source_ids'] or (sid,location) not in verified:raise ValueError('archive anchor not declared')
            source_proofs.append({'source_id':sid,'path':registry[sid]['path'],'source_context':registry[sid],**verified[(sid,location)]})
        first=row['source_ids'][0];main=[p for p in source_proofs if p['source_id']==first]
        proof={'anchor':'、'.join(p['anchor'] for p in main),'numbering':main[0]['numbering'],'source_sha256':registry[first]['sha256'],'lines':[line for p in main for line in p['lines']]}
        links=[];explicit=[];unresolved=[]
        for index,id in enumerate(row['people_ids']):
            role=('from_person' if index==0 else 'to_person') if record_type=='documented_qa' else 'person'
            if id in people:
                links.append({'entity_id':people[id]['id'],'kind':'person','basis':'explicit_archive_person_id_and_source_bound_position'})
                explicit.append({'role':role,'source_id':id,'entity_id':people[id]['id'],'label':positions[id]['name'],'status':'source_bound_navigation_not_new_identity_confirmation'})
            else:unresolved.append({'reference':id,'role':role,'reason':'source_scoped_position_only; same-name candidate not linked'})
        r={'id':row['id'],'origin':'archive_158_private_process_records','project':row['project'],'event':row['event_id'],'people':row['people_labels'],'relation':record_type,'strength':row['evidence_status'],'claim':row['claim'],'source':registry[first]['path'],'anchor':proof['anchor'],'limit':row['uncertainty'],'event_date':None,'event_time_note':'Actual process/recording date unknown; publication date is not event date','interview_date':None,'publication_date':row['publication_date'],'original_record':row,'verification_level':'documented_turns_not_audio_checked' if record_type=='documented_qa' else 'reported'}
        if record_type=='documented_qa':r.update(from_person=row['people_labels'][0],to_person=row['people_labels'][1])
        context={'source_navigation':[{'id':identity_id('archive-source',sid),'label':sid} for sid in row['source_ids']],'record_type':record_type,'newness':row['newness'],'event_count_warning':d['event_count_warning'],'source_family':family,'additional_source_evidence':[p for p in source_proofs if p['source_id']!=first],'source_people':[{'id':identity_id('archive-person',id),'name':positions[id]['name'],'upstream_id':id,'global_person_created':False} for id in row['people_ids']],'scoped_aliases':[a for a in aliases if (a.get('person_id') or a.get('source_position_id')) in row['people_ids']],'biometric_identification':False}
        if record_type=='noncausal_comparison':context.update(causal_connection_confirmed=False,implementation_independently_verified=False,chronology_known=False)
        if record_type=='creative_self_report' and row==rows[0]:context['upstream_image_ledger']={'records':json.loads(payload['刊载图来源.json']),'current_import_visual_review':False,'evidence_basis':'upstream reported observations; package bytes verified, no new viewing or face identification'}
        body={'record':r,'event_title':row['title'],'layer_schema':d['schema'],'review_only':True,'visibility':'local_private','event_is_base_graph_fact':False,'identity_policy':'Explicit source-bound IDs only; DOC positions and same-name candidates never become global identities; aliases remain manuscript-scoped','event_model':d['event_count_warning'],'mapping':{'links':links,'explicit_people':explicit,'unresolved':unresolved,'name_only_merge':False,'unmapped_people_labels_retained':row['people_labels']},'source_evidence':proof,'source_nature':row['evidence_status'],'direct_dialogue_record':record_type=='documented_qa','both_sides_independently_confirmed':False,'direction':'explicit_from_to' if record_type=='documented_qa' else 'not_structured','archive_context':context}
        if record_type=='documented_qa':body['source_direction_labels']={'from_person':row['people_labels'][0],'to_person':row['people_labels'][1]}
        if record_type=='noncausal_comparison':body['research_comparisons']=[{'type':'proposal_and_organizational_retrospective','claim':row['claim'],'records':[rows[1]['id'],row['id']],'causal':False}]
        prepared.append((stable_id('review','p4-private-people-review',row['id']),row['id'],body))
    pid=stable_id('review-package','p4-private-archive',manifest_sha);receipt={'schema':d['schema'],'review_only':True,'manifest_sha256':manifest_sha,'data_member':'delta_158_系统研究增量.json','record_ids':[r[0] for r in prepared],'base_graph_sha256':d['base89_sha256'],'protected_packages':protected,'source_hashes':{r['path']:r['sha256'] for r in registry.values()},'source_families':[family],'record_types':TYPES,'new_graph_events':0,'new_global_people':0}
    return persist_archive(db,root,manifest_sha,payload,manifest_raw,local_dir,pid,receipt,prepared,side,{'schema':d['schema'],'protected_packages':protected,'source_family':family,'new_global_people':0},{'records':len(prepared),'identity_navigation_records':len(side),'source_files':len(registry),'new_primary_evidence_families':1,'matched_mirror_paragraphs':len(prose),'new_graph_events':0,'new_global_people':0,'public_grants_added':0})

def persist_archive(db,root,manifest_sha,payload,manifest_raw,local_dir,pid,receipt,prepared,side,side_receipt,result):
    prior=db.execute('SELECT * FROM review_package WHERE id=?',(pid,)).fetchone()
    for id,_,body in prepared:
        old=db.execute('SELECT digest,package_id FROM review_record WHERE id=?',(id,)).fetchone()
        if old and (old['digest']!=digest(body) or old['package_id']!=pid):raise ValueError('archive review ID collision')
        if prior and not old:raise ValueError('archive replay missing record')
    if prior and prior['digest']!=digest(receipt):raise ValueError('archive replay receipt drift')
    def side_import(commit=True):return _persist(db,root,manifest_sha,payload,manifest_raw,local_dir,side,side_receipt,commit=commit)
    if prior:
        if validate_review_layer(db):raise ValueError('archive review integrity failed')
        side_import();return {'status':'already_imported',**result}
    with db:
        db.execute('INSERT INTO review_package VALUES(?,?,?,?,?,?)',(pid,manifest_sha,canonical(receipt),digest(receipt),'research_snapshots/identity-'+manifest_sha,now()))
        for id,upstream,body in prepared:
            raw=canonical(body);sha=digest(body);at=now()
            db.execute('INSERT INTO review_record VALUES(?,?,?,?,?,?,?)',(id,upstream,pid,raw,sha,1,at))
            db.execute('INSERT INTO review_revision VALUES(?,?,?,?,?,?)',(id,1,pid,raw,sha,at))
            for item in body['mapping']['links']:db.execute('INSERT INTO review_entity_link VALUES(?,?,?,?)',(id,item['entity_id'],item['kind'],item['basis']))
        side_import(commit=False)
        audit(db,'import_private_archive',{'package_id':pid,'records':len(prepared),'identity_navigation_records':len(side),'new_graph_events':0,'new_global_people':0})
    return {'status':'imported',**result}



def validate_archive_context(db,body):
    c=body.get('archive_context')
    if c is None:return []
    errors=[]
    if c.get('biometric_identification') is not False or c.get('source_family',{}).get('mirror_is_independent_witness') is not False or c.get('source_family',{}).get('independent_evidence_families')!=1 or body.get('event_is_base_graph_fact') is not False:errors.append({'code':'archive_scope_integrity'})
    if c.get('record_type')=='noncausal_comparison' and any(c.get(k) is not False for k in ['causal_connection_confirmed','implementation_independently_verified','chronology_known']):errors.append({'code':'archive_comparison_overclaim'})
    if c.get('event_group') and (any(c.get(k) is not False for k in ['implementation_confirmed','acceptance_or_rejection_confirmed','global_identity_confirmed']) or c['event_group'].get('new_confirmed_whole_events')!=0):errors.append({'code':'process_stage_overclaim'})
    targets=c.get('source_navigation',[])+c.get('source_people',[])+c.get('version_candidates',[])+([c['amendment']] if c.get('amendment') else [])
    for r in targets:
        if not db.execute('SELECT 1 FROM identity_record WHERE id=?',(r['id'],)).fetchone():errors.append({'code':'archive_navigation_broken'})
    return errors
