"""Small local workflow overlays on existing immutable private records. No publishing."""
import csv,io,json,re,hashlib
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from .core import canonical,digest,file_hash,stable_id,enqueue,now
from .review import _member_payloads,bounded_read,excerpt_source
from .identity import _persist,_record,identity_id,link,identity_detail

STATES={
 'workflow-batch':{'imported':{'validated'},'validated':{'revision_needed'},'revision_needed':{'validated'}},
 'workflow-concept':{'hypothesis':{'counterevidence_needed','revised','withdrawn'},'counterevidence_needed':{'revised','withdrawn'},'revised':{'counterevidence_needed','withdrawn'}},
 'workflow-queue':{'queued':{'scoped','paused'},'scoped':{'reviewing','paused'},'reviewing':{'bounded_complete','paused'},'paused':{'scoped'},'bounded_complete':{'scoped'}},
 'workflow-article':{'private_draft':{'evidence_review','revision_needed'},'evidence_review':{'revision_needed'},'revision_needed':{'evidence_review'},'editorial_review_required':{'awaiting_receipt','revision_needed'},'awaiting_receipt':{'revision_needed'},'published_verified':{'revision_needed','retracted'}},
}

def table(raw):return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
def time_range(value):
    m=re.fullmatch(r'(\d+:\d+:\d+(?:\.\d+)?)–(\d+:\d+:\d+(?:\.\d+)?)',value)
    if not m:return None
    def seconds(v):h,mi,s=v.split(':');return int(h)*3600+int(mi)*60+float(s)
    return [seconds(m[1]),seconds(m[2])]

def frozen_bundle(local_dir,kind,files):
    spec={n:hashlib.sha256(b).hexdigest() for n,b in files.items()};raw=canonical(spec).encode();sha=hashlib.sha256(raw).hexdigest();root=Path(local_dir)/'workflow_inputs'/kind/sha;root.mkdir(parents=True,exist_ok=True)
    for name,value in {**files,'MANIFEST.json':raw}.items():
        p=root/name
        if p.exists() and p.read_bytes()!=value:raise ValueError('workflow frozen input collision')
        if not p.exists():p.write_bytes(value)
    return root,sha,files,raw


def import_workflow(db,folder,manifest_sha,local_dir):
    root=Path(folder).resolve();payload,_=_member_payloads(root,manifest_sha)
    if not db.execute('SELECT 1 FROM review_package WHERE manifest_sha256=?',(manifest_sha,)).fetchone():raise ValueError('research package must be imported first')
    verification=json.loads(payload['长期准则保存与读回核验.json']);protocol=bounded_read(verification['fixed_path']);registry=bounded_read(verification['registry'])
    if hashlib.sha256(protocol).hexdigest()!=verification['sha256'] or hashlib.sha256(registry).hexdigest()!=verification['registry_sha256'] or bounded_read(verification['versioned_path'])!=protocol:raise ValueError('workflow protocol or registry version drift')
    template=json.loads(payload['每批事件研究模板.json']);version=verification['version'];entries=table(registry);counts=Counter(r['namespace'] for r in entries)
    if counts!={'artist_archive':verification['artist_index_entries'],'b_public_library':verification['b_index_entries']} or len(entries)!=verification['total_namespaced_entries'] or len({r['registry_key'] for r in entries})!=len(entries) or verification['not_distinct_project_count'] is not True or template['protocol_version']!=version:raise ValueError('workflow namespaced registry mismatch')
    for e in entries:
        prefix={'artist_archive':'artist:','b_public_library':'b:'}[e['namespace']]
        if e['registry_key']!=prefix+e['project_key'] or e['protocol_version']!=version:raise ValueError('workflow cross-site identity must stay namespaced')
    batch='research-162';protocol_key='protocol/'+version;batchkey=batch+'/'+manifest_sha;records=[]
    def add(kind,key,data,refs=()):
        records.append(_record(kind,key,data,{},links=list(refs)));return link(kind,key)
    protocol_ref=add('workflow-protocol',protocol_key,{'title':'事件研究长期准则 '+version,'state':'fixed_reference','version':version,'text':protocol.decode(),'source_path':verification['fixed_path'],'sha256':verification['sha256'],'publication_scope':'private_reference'})
    template_ref=add('workflow-template','template/'+version,{'title':'每批事件研究模板 '+version,'state':'fixed_reference','template':template},[protocol_ref])
    registry_refs=[]
    for e in entries:registry_refs.append(add('workflow-registry',version+'/'+e['registry_key'],{'title':e['title'],'state':'registered','registry_key':e['registry_key'],'namespace':e['namespace'],'original_row':e,'not_a_distinct_project_count':True,'auto_run':False},[protocol_ref]))
    reviews=[]
    for row in db.execute('SELECT id,upstream_id,body FROM review_record'):
        b=json.loads(row['body'])
        if row['upstream_id'].startswith(('NEW160-','NEW161-','NEW162-')):reviews.append((row['id'],row['upstream_id'],b))
    evidence=[];evidence_refs={}
    for row in table(payload['文章逐段证据表.csv']):
        key=batch+'/evidence/'+row['anchor'];range_=time_range(row['location']);targets=[]
        for id,up,b in reviews:
            v=b.get('video_context',{})
            if v.get('source_path')==row['source']:
                if row['anchor'].startswith('A') and range_ and v['source_start_seconds']<range_[1] and v['source_end_seconds']>range_[0]:targets.append({'id':id,'kind':'review','target':'review'})
                if row['anchor'] in {'B01','B02'} and up=='NEW162-01' or row['anchor'] in {'B03','B04'} and up=='NEW162-02':targets.append({'id':id,'kind':'review','target':'review'})
        proof=None
        if row['anchor']=='D01':
            raw=bounded_read(row['source']);proof=excerpt_source(row['source'],raw,','.join('L'+n for n in re.findall(r'\d+',row['location'])));proof['hash_status']='observed_at_import';proof['public_permission_inferred']=False
        elif not targets:raise ValueError('workflow article evidence cannot resolve research source')
        r={'title':row['anchor']+' · '+row['claim'],'state':'source_bound','anchor':row['anchor'],'original_row':row,'range':range_,'source_evidence':proof,'locator_precision':'inherited article locator; not finer than underlying machine segments','reuses_claim_ids':[t['id'] for t in targets],'public_permission':False};ref=add('workflow-evidence',key,r,targets);evidence_refs[row['anchor']]=ref;evidence.append(ref)
    narrative=payload['完整事件叙述_从召集到尚未定型的共同工作_私有.md'].decode();paragraphs=[]
    for n,text in enumerate(narrative.split('\n\n'),1):
        refs=sorted(set(re.findall(r'\b[ABD]\d{2}\b',text)))
        if any(x not in evidence_refs for x in refs):raise ValueError('unmapped narrative evidence locator')
        paragraphs.append({'paragraph_id':f'P{n:03}','text':text,'evidence_ids':[evidence_refs[x]['id'] for x in refs],'review_ids':[id for id,up,b in reviews if up=='NEW162-03' and '限定范围的负结果' in text]})
    concept_text=next((p for p in paragraphs if '据此' in p['text'] and '解释' in p['text']),None)
    if not concept_text or '如果' not in concept_text['text']:raise ValueError('workflow concept requires explicit interpretation and falsifier')
    falsifier=concept_text['text'][concept_text['text'].index('如果'):]
    concept=add('workflow-concept',batch+'/concept/parallel-generation-recording',{'title':'活动生成与记录并行（假说）','state':'hypothesis','interpretation':concept_text['text'],'alternative_explanation':'尚未落实的方案，而非已实施方法','falsifying_evidence_needed':falsifier,'support_anchors':['A04','A05'],'source_paragraph_id':concept_text['paragraph_id'],'source_text_sha256':hashlib.sha256(payload['完整事件叙述_从召集到尚未定型的共同工作_私有.md']).hexdigest(),'status_basis':'explicit interpretation and conditional revision in supplied narrative','not_event_fact':True},[evidence_refs['A04'],evidence_refs['A05']])
    article=add('workflow-article',batch+'/private-narrative',{'title':'从召集到尚未定型的共同工作（私有事件稿）','state':'private_draft','publication_scope':'private_research','text':narrative,'text_sha256':hashlib.sha256(payload['完整事件叙述_从召集到尚未定型的共同工作_私有.md']).hexdigest(),'paragraphs':paragraphs,'publication':{'status':'private','auto_publish':False,'receipt':None,'public_source_mapping':None},'reuses_claim_ids':sorted({t['id'] for r in records if r['kind']=='workflow-evidence' for t in r['body']['links'] if t['target']=='review'})},evidence+[concept,protocol_ref]+[{'id':id,'kind':'review','target':'review'} for id,up,b in reviews if up=='NEW162-03'])
    handoff=json.loads(payload['website-handoff.json']);boundary=json.loads(payload['公开边界与逐句待核表.json'])
    if handoff['auto_publish'] is not False or handoff['private_research_export'] is not False or handoff['allowed_media'] or handoff['b_project_id'] is not None or handoff['video_mapping'] is not None:raise ValueError('workflow publishing boundary changed')
    method=add('workflow-article',batch+'/method-candidate',{'title':'共同工作如何留下历史（普通方法候选稿）','state':'editorial_review_required','publication_scope':'method_candidate','text':payload[handoff['candidate_markdown']].decode(),'text_sha256':hashlib.sha256(payload[handoff['candidate_markdown']]).hexdigest(),'paragraphs':[],'publication':{'status':'awaiting_solewriter_review_and_receipt','auto_publish':False,'receipt':None,'public_source_mapping':None,'allowed_media':[]},'handoff':handoff,'blocked_statements':boundary['pending_sentences'],'site_identity_boundary':'artist entry does not create a B site project; batch numbers are not video IDs'},[evidence_refs['D01'],protocol_ref])
    queue_refs=[];job_specs=[]
    for n,q in enumerate(json.loads(payload['下一批覆盖队列.json']),1):
        ref=add('workflow-queue',batch+'/queue/'+str(n),{'title':q['question'],'state':'queued','scope':q,'auto_execute':False,'stop_rule':q['stop_rule']},[article,protocol_ref]);queue_refs.append(ref);job_specs.append((ref['id'],q))
    add('workflow-batch',batchkey,{'title':'162事件研究与持续发表工作流','state':'imported','protocol_version':version,'registry_entries':len(entries),'namespace_counts':dict(counts),'not_distinct_projects':True,'research_manifest_sha256':manifest_sha,'reuses_packages':[160,161],'new_confirmed_events':0,'auto_execute':False,'auto_publish':False,'publication_receipts':0},[protocol_ref,template_ref,article,method,concept]+queue_refs+registry_refs)
    files={'workflow-records.json':canonical(records).encode(),'protocol.md':protocol,'registry.csv':registry,'origin.json':canonical({'package':str(root),'manifest_sha256':manifest_sha,'verification':verification}).encode()};frozen,sha,files,manifest=frozen_bundle(local_dir,'imports',files)
    with db:
        result=_persist(db,frozen,sha,files,manifest,local_dir,records,{'schema':'p4.private.research-workflow/1','input_manifest_sha256':manifest_sha},commit=False)
        for id,q in job_specs:enqueue(db,'bounded_research',None,canonical({'workflow_id':id,'question':q['question'],'source_path':q['source_path'],'max_scope':q['range'],'stop_rule':q['stop_rule'],'auto_execute':False}),q['priority'])
    return {**result,'workflow_records':len(records),'registry_entries':len(entries),'namespace_counts':dict(counts),'queue_items':len(job_specs),'publication_receipts':0,'batch_id':identity_id('workflow-batch',batchkey)}


def workflow_detail(db,id):
    d=identity_detail(db,id)
    if not d['kind'].startswith('workflow-') or d['kind']=='workflow-state':raise ValueError('workflow record required')
    history=[]
    for r in db.execute("SELECT id,body FROM identity_record WHERE kind='workflow-state'"):
        b=json.loads(r['body'])['record']
        if b['target_id']==d['id']:history.append({'id':r['id'],**b})
    history.sort(key=lambda x:x['sequence']);state=d['body']['record'].get('state','registered');token=digest({'record_digest':d['digest'],'state':state,'sequence':0})
    for n,h in enumerate(history,1):
        if h['sequence']!=n or h['expected_token']!=token or h['from_state']!=state:raise ValueError('workflow state history broken')
        state=h['to_state'];token=digest({'previous':token,'event':{k:v for k,v in h.items() if k!='id'}})
    d.update(state=state,state_token=token,state_history=history)
    if 'locator_binding' in d['body']['record']:
        from .locators import entity_navigation
        d['entity_navigation']=entity_navigation(db,d['body']['record'])
    return d


def workflow_list(db,q=''):
    rows=[];counts=Counter()
    for r in db.execute("SELECT id,kind FROM identity_record WHERE kind LIKE 'workflow-%' AND kind!='workflow-state' ORDER BY kind,upstream_id"):
        d=workflow_detail(db,r['id']);b=d['body']['record'];counts[r['kind']]+=1
        if q and q.casefold() not in canonical(b).casefold() and q.casefold() not in r['kind']:continue
        rows.append({'id':r['id'],'kind':r['kind'],'title':b.get('title',d['upstream_id']),'state':d['state'],'namespace':b.get('namespace'),'publication_scope':b.get('publication_scope')})
    return {'records':rows,'count':len(rows),'kind_counts':dict(counts),'scope':'private local workflow; site entries are not distinct projects'}


def _record_action(db,local_dir,target,next_state,expected,reason,receipt=None,extra_files=None):
    d=workflow_detail(db,target);r=d['body']['record']
    if expected!=d['state_token'] or not reason.strip():raise ValueError('stale workflow state or missing reason')
    if receipt is None and next_state not in STATES.get(d['kind'],{}).get(d['state'],set()):raise ValueError('invalid workflow transition; publishing requires a verified receipt')
    if r.get('publication_scope')=='private_research' and next_state in {'awaiting_receipt','published_verified'}:raise ValueError('private research cannot become a public method draft')
    event={'target_id':d['id'],'sequence':len(d['state_history'])+1,'from_state':d['state'],'to_state':next_state,'expected_token':expected,'reason':reason,'receipt':receipt,'does_not_execute_or_publish':True};key=d['id']+'/'+str(event['sequence'])+'/'+digest(event);row=_record('workflow-state',key,event,{},links=[{'id':d['id'],'kind':d['kind'],'target':'identity'}]);files={'action.json':canonical(event).encode(),**(extra_files or {})};root,sha,payload,manifest=frozen_bundle(local_dir,'actions',files);_persist(db,root,sha,payload,manifest,local_dir,[row],{'schema':'p4.private.workflow-state/1'},commit=False);return workflow_detail(db,d['id'])


def record_action(db,local_dir,target,next_state,expected,reason,receipt=None,extra_files=None):
    if db.in_transaction:raise ValueError('workflow state update requires its own transaction')
    with db:
        db.execute('BEGIN IMMEDIATE')
        return _record_action(db,local_dir,target,next_state,expected,reason,receipt,extra_files)


def import_receipt(db,local_dir,target,path,sha,expected):
    raw=bounded_read(str(Path(path).resolve()))
    if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('publication receipt hash mismatch')
    receipt=json.loads(raw);d=workflow_detail(db,target);r=d['body']['record']
    if d['kind']!='workflow-article' or r.get('publication_scope')!='method_candidate' or d['state'] not in {'editorial_review_required','awaiting_receipt','published_verified'}:raise ValueError('only method candidates accept a first publication receipt')
    required=['schema','document_id','document_digest','scope','publisher','approval_evidence_path','approval_evidence_sha256','url','deployment_id','published_content_sha256','readback_path','readback_at','checks']
    if any(k not in receipt for k in required) or receipt['schema']!='p4.workflow.publication-receipt/1' or receipt['document_id']!=d['id'] or receipt['document_digest']!=d['digest'] or receipt['scope']!='method_only' or receipt['publisher']!='solewriter':raise ValueError('publication receipt scope or revision mismatch')
    url=urlsplit(receipt['url'])
    if url.scheme!='https' or not url.netloc or url.username or not receipt['deployment_id'] or any(receipt['checks'].get(k) is not True for k in ('links_verified','method_only','private_material_excluded')):raise ValueError('publication readback incomplete')
    if datetime.fromisoformat(receipt['readback_at']).tzinfo is None:raise ValueError('publication receipt needs timezone')
    approval=bounded_read(receipt['approval_evidence_path']);readback=bounded_read(receipt['readback_path'])
    if hashlib.sha256(approval).hexdigest()!=receipt['approval_evidence_sha256'] or hashlib.sha256(readback).hexdigest()!=receipt['published_content_sha256']:raise ValueError('publication evidence hash mismatch')
    files={'receipt.json':raw,'approval-evidence.txt':approval,'published-readback.txt':readback}
    if 'public_edition' in receipt:
        from .public_method import validate_payload
        edition=receipt['public_edition'];edition_raw=bounded_read(edition['payload_path']);payload=json.loads(edition_raw);article=validate_payload(payload);review=json.loads(approval)
        if hashlib.sha256(edition_raw).hexdigest()!=edition['payload_sha256'] or edition['relation']!='public_editorial_revision_of_candidate' or edition['article_id']!=article['id'] or edition['article_version']!=article['articleVersion'] or receipt['url']!=payload['urls']['fixed_zh'] or receipt['deployment_id']!=payload['deployment']:raise ValueError('published editorial edition mismatch')
        if review['article_id']!=article['id'] or review['article_version']!=article['articleVersion'] or review['private_full_article_read'] is not False or review['private_video_or_machine_text_read'] is not False or review['allowed_images'] or set(review['excluded_claims'])!=set(r['blocked_statements'][i]['id'] for i in range(len(r['blocked_statements']))):raise ValueError('editorial review scope mismatch')
        if receipt.get('published_article')!=article or receipt.get('published_urls')!=payload['urls']:raise ValueError('published article is not the frozen edition')
        original_package=json.loads(db.execute('SELECT body FROM identity_package WHERE id=?',(d['package_id'],)).fetchone()[0])
        if review['manifest_sha256']!=original_package['input_manifest_sha256']:raise ValueError('editorial approval belongs to a different research handoff')
        files['published-payload.json']=edition_raw
    if d['state']=='published_verified':
        if d['state_history'][-1]['receipt']!=receipt:raise ValueError('publication replay differs from recorded receipt')
        return d
    return record_action(db,local_dir,target,'published_verified',expected,'Recorded supplied solewriter approval and publication readback; no publication performed',receipt,files)


def validate_workflow(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_record'").fetchone():return []
    errors=[]
    for r in db.execute("SELECT id,body FROM identity_record WHERE kind LIKE 'workflow-%' AND kind!='workflow-state'"):
        try:
            d=workflow_detail(db,r['id']);b=d['body']['record']
            if b.get('auto_publish') is True or b.get('auto_execute') is True or b.get('publication',{}).get('auto_publish') is True:raise ValueError('workflow cannot auto-execute')
            if d['state']=='published_verified' and (b.get('publication_scope')!='method_candidate' or not d['state_history'][-1]['receipt']):raise ValueError('publication receipt required')
        except (ValueError,KeyError,TypeError):errors.append({'code':'workflow_integrity','id':r['id']})
    return errors
