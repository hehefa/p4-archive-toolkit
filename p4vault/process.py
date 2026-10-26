"""Private manuscript stages; partial excerpts and version candidates retain their limits."""
import json
import re
from pathlib import Path
from .core import canonical,digest,stable_id
from .review import _member_payloads,bounded_read
from .identity import _record,direct_paragraphs,identity_id,link,review_link
from .archive import _protected,persist_archive

STAGE_TYPES=['participation_self_report','documented_work_qa','proposal_sequence','principle_response','operating_conditions_qa']

def paragraph_numbers(value):
    parts=re.findall(r'P?(\d+)(?:[–-](\d+))?',value);numbers=[]
    for a,b in parts:
        start=int(a);end=int(b or a)
        if start<1 or end<start or end-start>100:raise ValueError('invalid process paragraph range')
        numbers.extend(range(start,end+1))
    if not numbers:raise ValueError('process paragraph reference missing')
    return numbers

def import_process(db,folder,manifest_sha,local_dir):
    root=Path(folder).resolve();payload,manifest_raw=_member_payloads(root,manifest_sha);d=json.loads(payload['delta_159_独立研究增量.json'])
    if d.get('schema')!='p4.private.process-increment/159' or d.get('visibility')!='local_private' or d.get('new_confirmed_whole_events')!=0 or d.get('replaced_records') or d.get('global_identity_merges'):raise ValueError('private nonmerging process increment required')
    dependencies=_protected(db,root,local_dir,{'base89_sha256':d['base89_sha256'],'protected_base_manifests':d['protected']})
    rows=d['added_stage_records'];positions=d['document_positions'];sources=json.loads(payload['来源登记.json']);anchors=json.loads(payload['工作过程锚点_私有.json']);supplement=json.loads(payload['版本对应与158增量口径补充.json']);verification=json.loads(payload['核验结果.json'])
    if rows!=json.loads(payload['五阶段问答记录.json']) or positions!=json.loads(payload['文稿参与位置_未强配实名.json']) or supplement['relations']!=d['version_relation_candidates'] or d['sources']!=sources[:1]:raise ValueError('process package member disagreement')
    if [r['id'] for r in rows]!=[f'NEW159-{i:02}' for i in range(1,6)] or len(sources)!=2 or len({s['source_id'] for s in sources})!=2:raise ValueError('unsupported process layout')
    raw_sources={};paragraphs={}
    for s in sources:
        raw=bounded_read(s['path'])
        if __import__('hashlib').sha256(raw).hexdigest()!=s['sha256']:raise ValueError('process source hash mismatch')
        raw_sources[s['source_id']]=raw;paragraphs[s['source_id']]=direct_paragraphs(raw)
    primary,recheck=sources;sid=primary['source_id'];family_id=primary['family_id'];count=len(paragraphs[sid]);old_count=len(paragraphs[recheck['source_id']])
    if count!=verification['new_doc_nonempty_paragraphs'] or old_count!=verification['old_doc_rechecked_paragraphs']:raise ValueError('process paragraph count mismatch')
    proof={}
    for a in anchors:
        p=a['P']
        if type(p) is not int or not 1<=p<=count or p in proof:raise ValueError('invalid or duplicate process anchor')
        actual=paragraphs[sid][p-1]
        if a['xml_body_p']!=actual['xml_body_p']:raise ValueError('process XML position mismatch')
        if a.get('is_full_paragraph') is True:
            if a['text']!=actual['text']:raise ValueError('process exact paragraph mismatch')
            text=a['text']
        elif a.get('is_full_paragraph') is False:
            text=a.get('exact_excerpt')
            if not isinstance(text,str) or not text or text not in actual['text']:raise ValueError('process declared excerpt mismatch')
        else:raise ValueError('process excerpt boundary missing')
        # Never persist the unselected remainder of a deliberately shortened paragraph.
        proof[p]={'location':'P'+str(p),'xml_body_p':a['xml_body_p'],'text':text,'is_full_paragraph':a['is_full_paragraph']}
    if {n for r in rows for n in r['paragraphs']}!=set(proof):raise ValueError('process retained work anchors disagree')
    groups={r['event_group_id'] for r in rows}
    if len(groups)!=1 or d['idempotency_key']!=family_id:raise ValueError('process group/family mismatch')
    group_id=next(iter(groups));people={r['id']:r for r in positions}
    if len(people)!=len(positions) or any(p.get('canonical_person_id') is not None or p['scope']!=family_id or not p['id'].startswith('DOC159-') for p in positions):raise ValueError('process labels cannot become global identities')
    for r in rows:
        if r['source_id']!=sid or r['source_family']!=family_id or r.get('new_event_count')!=0 or r.get('event_date') is not None or r['visibility']!='local_private' or len(r['paragraphs'])!=len(set(r['paragraphs'])) or any(p not in people for p in r['participant_position_ids']):raise ValueError('process record scope invalid')
        if r['participants']!=[people[id]['label'] for id in r['participant_position_ids']]:raise ValueError('process position labels disagree')
    study=payload['完整阶段研究_提议与原则回应_私有.md'].decode();match=re.match(r'# 《([^》]+)》',study)
    if not match:raise ValueError('process project heading missing')
    project=match[1]
    prior_report=bounded_read(supplement['old135_report']);prior_report_sha=__import__('hashlib').sha256(prior_report).hexdigest()
    old_reviews=[]
    for head in db.execute('SELECT id,package_id,body FROM review_record'):
        b=json.loads(head['body'])
        if b.get('layer_schema')=='p4.private.archive-increment/158' and head['package_id'] in {x['package_id'] for x in dependencies if x['kind']=='review'}:old_reviews.append((head['id'],b))
    if not old_reviews:raise ValueError('declared process comparison baseline missing')
    family={'family_id':family_id,'source_ids':[sid],'family_kind':'single_manuscript','document_nonempty_paragraphs':count,'retained_work_paragraphs':len(proof),'independent_evidence_families':1,'mirror_is_independent_witness':False,'new_independent_response_confirmed':False,'publication_relations_confirmed':False,'scope':'one manuscript source family; links to published extracts remain candidates'}
    group={'group_id':group_id,'title':'同一文稿的阶段组','stage_ids':[r['id'] for r in rows],'stage_count':len(rows),'source_family':family_id,'event_date':None,'date_hint':primary['date'],'new_confirmed_whole_events':0}
    amendment_id='AMEND159-NOVELTY158';version_keys=[family_id+'/version/'+str(i+1) for i in range(len(d['version_relation_candidates']))];side=[];versions=[]
    for key,candidate in zip(version_keys,d['version_relation_candidates']):
        m=re.fullmatch(r'(\S+) (L\d+(?:/L\d+)*)',candidate['published'])
        if not m:raise ValueError('process published candidate reference invalid')
        source_id=m[1];wanted={'L'+str(n) for n in paragraph_numbers(m[2])};source=db.execute("SELECT body FROM identity_record WHERE kind='archive-source' AND upstream_id=?",(source_id,)).fetchone()
        if not source:raise ValueError('process published source not imported')
        source=json.loads(source[0])['record'];targets=[];covered=set()
        for id,b in old_reviews:
            found={x['location'] for x in b['source_evidence']['lines']}&wanted
            if b['record']['source']==source['path'] and b['source_evidence']['source_sha256']==source['sha256'] and found:targets.append(id);covered|=found
        if covered!=wanted:raise ValueError('process candidate published anchors unavailable')
        rawref=candidate['raw'];old=re.match(r'^(\d{8}) (P.*)$',rawref)
        if old:
            if not Path(recheck['path']).name.startswith(old[1]):raise ValueError('process recheck filename mismatch')
            nums=paragraph_numbers(old[2]);source_nav=link('process-source',recheck['source_id']);stage_ids=[]
            if max(nums)>old_count:raise ValueError('process recheck anchor outside source')
        else:
            nums=paragraph_numbers(rawref);source_nav=link('process-source',sid);stage_ids=[r['id'] for r in rows if set(r['paragraphs'])&set(nums)]
            if not set(nums)<=set(proof):raise ValueError('process candidate outside retained work scope')
        refs=[source_nav,link('archive-source',source_id)]+[review_link(id) for id in stage_ids]+[{'id':id,'kind':'review','target':'review'} for id in targets]
        side.append(_record('process-version',key,candidate,{},links=refs,source_paragraphs=nums,stage_ids=stage_ids,global_identity_confirmed=False,same_interview_confirmed=False,counts_as_independent_response=False,chronology_confirmed=False))
        versions.append({'id':identity_id('process-version',key),'label':candidate['status'],'stage_ids':stage_ids})
    side.append(_record('archive-amendment',amendment_id,supplement,{},links=[{'id':id,'kind':'review','target':'review'} for id,_ in old_reviews]+[link('process-version',key) for key in version_keys]+[link('process-source',recheck['source_id'])],replaces_original_records=False,new_events=0,prior_report_evidence={'path':supplement['old135_report'],'sha256':prior_report_sha,'hash_status':'observed_at_import','counted_as_new_research':False}))
    side.append(_record('process-family',family_id,family,{},links=[link('process-source',sid),link('process-group',group_id)]+[link('process-version',key) for key in version_keys]))
    side.append(_record('process-group',group_id,group,{},links=[review_link(r['id']) for r in rows]+[link('process-source',sid),link('process-family',family_id),link('archive-amendment',amendment_id)]))
    for s in sources:
        is_primary=s['source_id']==sid;refs=([link('process-person',p['id']) for p in positions]+[link('process-group',group_id),link('process-family',family_id)]) if is_primary else [link('archive-amendment',amendment_id)]
        evidence={'anchor':'、'.join('P'+str(n) for n in sorted(proof)) if is_primary else 'prior-study recheck','numbering':'nonempty direct-body Word paragraphs, one-based; XML position retained','source_sha256':s['sha256'],'lines':[proof[n] for n in sorted(proof)] if is_primary else [],'retained_scope':'selected work-process paragraphs only; no full manuscript copy' if is_primary else 'source hash and paragraph count verified; not imported as new process evidence'}
        side.append(_record('process-source',s['source_id'],s,{},links=refs,source_evidence=evidence))
    for p in positions:
        refs=[review_link(r['id']) for r in rows if p['id'] in r['participant_position_ids']]+[link('process-source',sid),link('process-group',group_id)]
        side.append(_record('process-person',p['id'],p,{},links=refs,global_person_created=False,global_identity_confirmed=False))
    prepared=[]
    for index,(r,kind) in enumerate(zip(rows,STAGE_TYPES),1):
        evidence={'anchor':'、'.join('P'+str(n) for n in r['paragraphs']),'numbering':'nonempty direct-body Word paragraphs, one-based; XML position retained','source_sha256':primary['sha256'],'lines':[proof[n] for n in r['paragraphs']]}
        record={'id':r['id'],'origin':'process_159_manuscript_stages','project':project,'event':group_id,'people':r['participants'],'relation':kind,'strength':r['type'],'claim':r['claim'],'source':primary['path'],'anchor':evidence['anchor'],'limit':r['limit'],'event_date':None,'event_time_note':r['date_hint'],'interview_date':None,'publication_date':None,'original_record':r,'verification_level':'documented_turns_not_audio_checked'}
        context={'record_type':kind,'newness':primary['novelty'],'event_count_warning':'五条阶段记录共用一个文稿事件组，不是五场新事件；未证实采纳、拒绝、圆桌、主持或开会实施。','source_family':family,'source_navigation':[{'id':identity_id('process-source',sid),'label':sid},{'id':identity_id('process-group',group_id),'label':group_id}],'source_people':[{'id':identity_id('process-person',id),'name':people[id]['label'],'upstream_id':id,'global_person_created':False} for id in r['participant_position_ids']],'scoped_aliases':[],'additional_source_evidence':[],'biometric_identification':False,'event_group':group,'stage_number':index,'version_candidates':[v for v in versions if r['id'] in v['stage_ids']],'amendment':{'id':identity_id('archive-amendment',amendment_id),'label':'158增量口径补充：保留原记录与版本候选'},'implementation_confirmed':False,'acceptance_or_rejection_confirmed':False,'global_identity_confirmed':False}
        body={'record':record,'event_title':r['title'],'layer_schema':d['schema'],'review_only':True,'visibility':'local_private','event_is_base_graph_fact':False,'identity_policy':'All four manuscript labels remain source-scoped positions; no global identities or video speakers inferred','event_model':context['event_count_warning'],'mapping':{'links':[],'explicit_people':[],'unresolved':[{'reference':id,'role':'source_label','reason':'manuscript-scoped position; canonical identity unconfirmed'} for id in r['participant_position_ids']],'name_only_merge':False,'unmapped_people_labels_retained':r['participants']},'source_evidence':evidence,'source_nature':r['type'],'direct_dialogue_record':True,'both_sides_independently_confirmed':False,'direction':'documented_multi_party_turns','archive_context':context}
        prepared.append((stable_id('review','p4-private-people-review',r['id']),r['id'],body))
    pid=stable_id('review-package','p4-private-process',manifest_sha);receipt={'schema':d['schema'],'review_only':True,'manifest_sha256':manifest_sha,'data_member':'delta_159_独立研究增量.json','record_ids':[r[0] for r in prepared],'base_graph_sha256':d['base89_sha256'],'protected_packages':dependencies,'source_hashes':{s['path']:s['sha256'] for s in sources},'source_families':[family],'event_groups':[group],'record_types':STAGE_TYPES,'new_graph_events':0,'new_global_people':0,'prior_report_sha256':prior_report_sha}
    return persist_archive(db,root,manifest_sha,payload,manifest_raw,local_dir,pid,receipt,prepared,side,{'schema':d['schema'],'protected_packages':dependencies,'source_family':family,'new_global_people':0},{'records':len(prepared),'identity_navigation_records':len(side),'new_document_families':1,'document_event_groups':1,'new_graph_events':0,'new_global_people':0,'public_grants_added':0,'retained_work_paragraphs':len(proof),'partial_paragraphs':sum(not p['is_full_paragraph'] for p in proof.values())})
