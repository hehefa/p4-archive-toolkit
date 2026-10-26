"""Append bounded same-source batches; never execute media processing or publication."""
import csv,io,json,re,hashlib
from pathlib import Path
from .core import stable_id,digest,file_hash
from .review import _member_payloads
from .identity import _record,identity_id,link,review_link,review_id
from .archive import _protected,persist_archive
from .video import verify_media,segments,interval,close,hms,video_timeline
from .workflow import workflow_detail

PROFILES={163:('delta_163_稳定独立增量.json','覆盖与方法质量日志.json','新增机稿_原片时轴_私有未听校.json','来源与范围复核.json','事件研究_听到故事之后还缺哪一步_私有.md','逐段证据与限制.csv'),164:('delta_164_稳定独立增量.json','164方法质量日志.json','新增尾段机稿_原片时轴_未听校.json','来源复核.json','同源完整叙事_从分享回应到仍未落实的工作_私有.md','尾段逐段证据.csv')}


def claim_labels(text):
    found=[]
    for m in re.finditer(r'NEW(\d+)-(\d+)((?:、\d{2})*)',text):
        found.extend('NEW'+m[1]+'-'+n for n in [m[2],*re.findall(r'\d{2}',m[3])])
    return list(dict.fromkeys(found))


def import_continuation(db,folder,sha,local_dir):
    root=Path(folder).resolve();payload,manifest=_member_payloads(root,sha);matches=[b for b,n in PROFILES.items() if n[0] in payload]
    if len(matches)!=1:raise ValueError('one supported same-source batch required')
    batch=matches[0];member,quality_member,index_member,source_member,narrative_member,csv_member=PROFILES[batch];d=json.loads(payload[member]);q=json.loads(payload[quality_member]);check=json.loads(payload[source_member]);rows=d['records'];sid=check['source_id']
    if d['protocol_version']!='v1.1' or d['new_confirmed_events']!=0 or d['new_named_people']!=0 or d['auto_publish'] is not False or [r['claim_id'] for r in rows]!=['NEW'+str(batch)+'-'+str(n).zfill(2) for n in range(1,(4 if batch==163 else 3)+1)]:raise ValueError('continuation scope or stable IDs invalid')
    head=db.execute("SELECT body FROM identity_record WHERE kind='video-source' AND upstream_id=?",(sid,)).fetchone()
    if not head:raise ValueError('162 source must already exist')
    source=json.loads(head[0])['record'];sample=verify_media(source);duration=source['duration_seconds'];group=next(json.loads(r['body'])['record'] for r in db.execute("SELECT body FROM identity_record WHERE kind='video-group'") if json.loads(r['body'])['record']['source_id']==sid);gid=next(r['upstream_id'] for r in db.execute("SELECT upstream_id,body FROM identity_record WHERE kind='video-group'") if json.loads(r['body'])['record']['source_id']==sid);family=group['source_family'];candidate=group['event_candidate_id'];group_ref=link('video-group',gid);source_ref=link('video-source',sid)
    protocol=db.execute("SELECT body FROM identity_record WHERE kind='workflow-protocol'").fetchone()
    if not protocol:raise ValueError('fixed workflow protocol missing')
    protocol=json.loads(protocol[0])['record'];protocol_sha=file_hash(protocol['source_path'])
    protected={}
    if batch==163:
        base=json.loads(payload['旧包与来源核验.json']);graph_sha=base['89_graph'];expected_protocol=base['protocol_v1.1_sha256']
        for name,value in base.items():
            if isinstance(value,dict):
                if value['mismatches']:raise ValueError('prior package changed')
                protected[name]=file_hash(root.parent/name/'MANIFEST.json')
        bounds=q['new_machine_input'];frames_at=q['stills_viewed'];prior_end=q['prior_162'][1];dependencies=d['reuses_claim_ids']
        if q['human_listened_seconds']!=0 or q['continuous_video_viewed_seconds']!=0 or q['new_machine_text_reviewed']!=bounds or not check['sample_matches162'] or not check['size_matches162'] or check['event_date'] is not None or check['new_start']!=bounds[0] or q['unread']!=[bounds[1],duration]:raise ValueError('continuation coverage overclaim')
        excluded=[{'start':a,'end':b,'reason':q['exclusion_reason']} for a,b in q['excluded_private_or_unreliable_ranges']];coarse=[];closure=None
    else:
        base=json.loads(payload['89与151保持核验.json']);graph_sha=base['89_graph'];deps=json.loads(payload['来源依赖与旧包核验.json']);expected_protocol=deps['protocol_sha256']
        if base['151_mismatches'] or d['auto_run_next_video'] is not False or d['source_branch_status']!='closed_with_unresolved_questions':raise ValueError('continuation branch scope invalid')
        if [x['batch'] for x in deps['previous_packages']]!=[162,163]:raise ValueError('162 then 163 dependency required')
        for prior in deps['previous_packages']:
            p=Path(prior['package']);protected[p.name]=prior['manifest_sha256']
            if p.parent.resolve()!=root.parent or Path(prior['delta_path']).parent.resolve()!=p.resolve() or file_hash(prior['delta_path'])!=prior['delta_sha256']:raise ValueError('dependency delta mismatch')
        bounds=q['new_range'];frames_at=q['new_stills_viewed'];prior_end=check['previous_input'][1];dependencies=d['depends_on_claims'];closure=json.loads(payload['162_163_164同源完整覆盖说明.json'])
        if q['human_listened'] is not False or q['continuous_video_viewed'] is not False or q['invalid_timing_segments'] or not check['sample_match'] or not check['size_match'] or check['bytes']!=source['size'] or not close(check['duration'],duration) or closure['event_complete'] is not False or closure['machine_input_uncovered'] or closure['human_listened_seconds']!=0 or closure['continuous_video_viewed_seconds']!=0 or closure['human_listening_uncovered']!=[0,duration]:raise ValueError('complete input must not imply complete interpretation')
        excluded=[{k:e[k] for k in ('start','end','reason')} for e in closure['quality_exclusions'] if e['batch']==batch];coarse=[]
    if protocol_sha!=expected_protocol or protocol_sha!=protocol['sha256']:raise ValueError('fixed protocol changed')
    protected_receipts=_protected(db,root,local_dir,{'base89_sha256':graph_sha,'protected_base_manifests':protected})
    prior_timeline=video_timeline(db,group_ref['id']);prior_cov=[c for c in prior_timeline['coverage'] if c['batch']<batch]
    if [c['batch'] for c in prior_cov]!=([162] if batch==163 else [162,163]) or not close(prior_cov[-1]['input_range'][1],bounds[0]) or not close(prior_end,bounds[0]) or check['path']!=source['path'] or not close(d['new_input_seconds'],bounds[1]-bounds[0]):raise ValueError('continuation has missing or overlapping input predecessor')
    interval(*bounds,duration)
    for id in dependencies:
        if not db.execute('SELECT 1 FROM review_record WHERE upstream_id=?',(id,)).fetchone():raise ValueError('inherited claim missing')
    raw,runs=segments(payload,root,source,duration);index=json.loads(payload[index_member]);text_key='text_uncorrected' if batch==163 else 'machine_text_uncorrected'
    if len(raw)!=len(index) or len(raw)!=q['new_segments'] or not close(runs[0]['start'],bounds[0]) or not close(runs[-1]['end'],bounds[1]):raise ValueError('continuation machine count/range mismatch')
    for r,x in zip(raw,index):
        start=int(next(v['start'] for v in runs if v['start']<=r['start']<v['end']));segment_id=str(start)+'-'+str(r['raw_segment_index']);r['segment_id']=segment_id
        if x['segment_id']!=segment_id or not close(x['start'],r['start']) or not close(x['end'],r['end']) or x['start_hms']!=hms(r['start']) or x['end_hms']!=hms(r['end']) or x[text_key]!=r['text'] or x['speaker_id'] is not None:raise ValueError('continuation source-zero index drift')
        if batch==163 and Path(x['raw_json']).resolve()!=Path(r['raw_json']).resolve() or batch==164 and (x['source_id']!=sid or x['private'] is not True):raise ValueError('continuation machine source mismatch')
    if batch==163:
        if [(r['start'],r['end'],r['segments']) for r in runs]!=[(r['start'],r['end'],r['segments']) for r in q['runs']] or any(r['invalid_timing_segment_ids'] or r['exit_code']!=0 or not r['source_stat_unchanged'] for r in q['runs']):raise ValueError('continuation run quality drift')
    else:
        byseg={r['segment_id']:r for r in raw}
        if not q['excluded_segment_ids'] or not q['coarse_question_segment_ids']:raise ValueError('declared distorted/coarse segments required')
        for id in q['excluded_segment_ids']:
            r=byseg[id]
            if not any(close(e['start'],r['start']) and close(e['end'],r['end']) for e in excluded):raise ValueError('distorted segment must be excluded whole')
        coarse=[[byseg[id]['start'],byseg[id]['end']] for id in q['coarse_question_segment_ids']]
        observed=[{'batch':c['batch'],**r} for c in prior_cov for r in c['runs']]+[{'batch':batch,**r} for r in runs]
        if [(r['batch'],r['start'],r['end'],r['segments']) for r in observed]!=[(r['batch'],r['start'],r['end'],r['segments']) for r in closure['ranges']] or sum(r['segments'] for r in observed)!=closure['total_machine_segments'] or closure['source_id']!=sid or not close(closure['container_duration'],duration):raise ValueError('cumulative source coverage mismatch')
    side=[];frames=[];coverage_key=sid+'/batch/'+str(batch);prepared=[];evidence_refs={};csv_rows=list(csv.DictReader(io.StringIO(payload[csv_member].decode('utf-8-sig'))))
    for seconds in frames_at:
        name='frame_'+str(seconds)+'.jpg';image=payload[name]
        if not bounds[0]<=seconds<bounds[1] or len(image)>2*1024*1024 or not image.startswith(b'\xff\xd8\xff') or not image.endswith(b'\xff\xd9'):raise ValueError('continuation frame invalid')
        key=sid+'/frame/'+str(seconds);frames.append({'id':identity_id('video-frame',key),'seconds':seconds,'source_id':sid});side.append(_record('video-frame',key,{'source_id':sid,'group_id':gid,'source_seconds':seconds,'source_path':source['path'],'purpose':'upstream registered still; no identity or completed action inference'}, {},links=[source_ref,group_ref],preview_ref={'manifest_sha256':sha,'path':name,'sha256':hashlib.sha256(image).hexdigest(),'actual_frame_seconds':seconds}))
    for row in rows:
        id=row['claim_id'];windows=[[row['start'],row['end']]] if batch==163 else row['anchors'];negative='negative' in row.get('stage',row.get('type',''));a=min(x[0] for x in windows);b=max(x[1] for x in windows)
        if row['source_id']!=sid or row['source_path']!=source['path'] or row['event_candidate_id']!=candidate or row['event_date'] is not None or row['person_id'] is not None or row['action_confirmed'] is not False or (batch==163 and row['visibility']!='private'):raise ValueError('continuation identity or action overclaim')
        for start,end in windows:
            interval(start,end,duration)
            if not bounds[0]<=start<end<=bounds[1]:raise ValueError('claim outside new input')
        matches=[r for r in csv_rows if r['claim_id']==id]
        if len(matches)!=len(windows) or [(r['start_hms'],r['end_hms']) for r in matches]!=[(hms(x),hms(y)) for x,y in windows] or any(r['source_path']!=source['path'] or r['claim']!=row['claim'] or r['limits']!=row['limits'] for r in matches):raise ValueError('paragraph evidence CSV drift')
        selected=[]
        if not negative:
            for r in raw:
                if any(x<=r['start'] and r['end']<=y+1e-6 for x,y in windows):
                    if any(e['start']<r['end'] and e['end']>r['start'] for e in excluded):raise ValueError('excluded machine text cannot support a claim')
                    # Keep exact private bytes in the frozen package, but do not extract names/personal histories into navigation prose.
                    selected.append({**{k:v for k,v in r.items() if k!='text'},'text':'[私有机稿定位；不在导航正文摘录个人经历或未核称呼。]','raw_text_sha256':hashlib.sha256(r['text'].encode()).hexdigest(),'location':hms(r['start'])+' → '+hms(r['end']),'use_status':'locator_only_private','coarse_timing':any(close(r['start'],x) and close(r['end'],y) for x,y in coarse),'human_listened':False})
            if not selected:raise ValueError('claim lacks bounded machine anchors')
        pos=row.get('speaker_position_id');position_ref=None
        if pos:
            position_ref=identity_id('video-position',pos);side.append(_record('video-position',pos,{'label':pos,'source_id':sid,'group_id':gid,'canonical_person_id':None,'rule':'local speech position; not reliable diarization or a distinct person'}, {},links=[source_ref,group_ref,review_link(id)],global_person_created=False))
        nearest=min(frames,key=lambda f:abs(f['seconds']-(a+b)/2));context={'source_id':sid,'source_path':source['path'],'source_family':family,'group_id':gid,'event_candidate_id':candidate,'group_ref':group_ref['id'],'source_ref':source_ref['id'],'position_ref':position_ref,'coverage_ref':identity_id('video-coverage',coverage_key),'source_start_seconds':a,'source_end_seconds':b,'duration_seconds':duration,'source_zero':'same original source zero; independent of adjacent source','sample_verification':sample,'frame':{**nearest,'within_stage':any(x<=nearest['seconds']<=y for x,y in windows)},'excluded_ranges':[e for e in excluded if e['start']<b and e['end']>a],'coarse_timing_ranges':[r for r in coarse if r[0]<b and r[1]>a],'evidence_windows':[] if negative else windows,'bounded_finding':negative,'new_confirmed_events':0,'human_listened_seconds':0,'continuous_video_viewed_seconds':0,'global_identity_confirmed':False,'implementation_confirmed':False,'meeting_end_confirmed':False}
        record={'id':id,'origin':'continuation_'+str(batch),'project':(re.findall(r'《([^》]+)》',source['path']) or ['Source study'])[0],'event':candidate,'people':[pos] if pos else [],'relation':row.get('type',row.get('stage')),'strength':row['evidence_level'],'claim':row['claim'],'source':str(root/index_member),'anchor':'; '.join(hms(x)+' → '+hms(y) for x,y in windows),'limit':row['limits'],'event_date':None,'event_time_note':'Independent source clock; no verified calendar date','interview_date':None,'publication_date':None,'original_record':row,'verification_level':'machine_unreviewed_no_diarization'}
        body={'record':record,'event_title':row.get('title',row['claim']),'layer_schema':'p4.private.continuation/'+str(batch),'review_only':True,'visibility':'local_private','event_is_base_graph_fact':False,'identity_policy':'No named/biometric identity; raw private lines retained only in snapshot','event_model':'Same candidate and same source, not new events','mapping':{'links':[],'explicit_people':[],'unresolved':[],'name_only_merge':False,'unmapped_people_labels_retained':[pos] if pos else []},'source_evidence':{'kind':'machine_timeline','anchor':record['anchor'],'numbering':'source-zero machine locators only; no personal narrative extracted','source_sha256':hashlib.sha256(payload[index_member]).hexdigest(),'lines':selected},'source_nature':'私有机稿定位与有限研究解释；未经听校','direct_dialogue_record':False,'both_sides_independently_confirmed':False,'direction':'unverified_machine_turns','video_context':context}
        prepared.append((review_id(id),id,body));key='research-'+str(batch)+'/evidence/'+id;evidence_refs[id]=link('workflow-evidence',key);side.append(_record('workflow-evidence',key,{'title':id+' · '+body['event_title'],'state':'source_bound','anchor':id,'original_row':{'source':str(root/index_member),'location':record['anchor'],'level':row['evidence_level'],'public_review':'private; no publication permission'},'range':[a,b],'source_evidence':None,'locator_precision':'Original coarse segments retained, not finer word times','reuses_claim_ids':[review_id(id)],'public_permission':False}, {},links=[review_link(id)]))
    coverage={'source_id':sid,'source_family':family,'group_id':gid,'batch':batch,'input_range':bounds,'input_seconds':bounds[1]-bounds[0],'machine_segments':len(raw),'runs':runs,'excluded':excluded,'coarse_timing_ranges':coarse,'tail_without_recognized_text':[raw[-1]['end'],bounds[1]] if raw[-1]['end']<bounds[1] else None,'human_listened_seconds':0,'continuous_video_viewed_seconds':0,'new_confirmed_events':0,'meeting_end_confirmed':False,'implementation_confirmed':False,'frame_ids':frames,'stage_ids':[r['claim_id'] for r in rows],'quality':q,'whole_input_covered':batch==164,'unread':[] if batch==164 else [bounds[1],duration],'original_media_copied':False}
    side.append(_record('video-coverage',coverage_key,coverage,{},links=[source_ref,group_ref]+[review_link(r['claim_id']) for r in rows]))
    if closure and sorted([f['seconds'] for c in prior_cov for f in c['frame_ids']]+frames_at)!=closure['stills_in_this_source']:raise ValueError('cumulative frame source mismatch')
    add_workflow(db,payload,batch,sha,side,evidence_refs,dependencies,rows,narrative_member,sid,gid,bounds,closure)
    receipt={'schema':'p4.private.continuation/'+str(batch),'review_only':True,'data_member':member,'manifest_sha256':sha,'record_ids':[r[0] for r in prepared],'protected_packages':protected_receipts,'new_graph_events':0,'new_global_people':0,'source_sample':sample,'source_id':sid,'input_range':bounds}
    return persist_archive(db,root,sha,payload,manifest,local_dir,stable_id('review-package','p4-private-continuation',sha),receipt,prepared,side,{'schema':receipt['schema'],'source_id':sid,'input_manifest_sha256':sha},{'records':len(prepared),'identity_navigation_records':len(side),'new_machine_segments':len(raw),'registered_stills':len(frames),'source_id':sid,'new_sources':0,'new_graph_events':0,'new_global_people':0,'public_grants_added':0})


def add_workflow(db,payload,batch,sha,side,evidence,dependencies,rows,narrative_member,sid,gid,bounds,closure):
    prefix='research-'+str(batch);text=payload[narrative_member].decode();paras=[];refs=list(evidence.values());available={r[0] for r in db.execute('SELECT upstream_id FROM review_record')}|set(evidence)
    for n,p in enumerate(text.split('\n\n'),1):
        labels=claim_labels(p)
        if '沿用162' in p:labels+=['NEW162-02','NEW162-03']
        if not set(labels)<=available:raise ValueError('narrative refers to missing claims')
        paras.append({'paragraph_id':f'P{n:03}','text':p,'evidence_ids':[evidence[id]['id'] for id in labels if id in evidence],'review_ids':[review_id(id) for id in labels if id not in evidence]})
    private=link('workflow-article',prefix+'/private-narrative');side.append(_record('workflow-article',prefix+'/private-narrative',{'title':text.splitlines()[0].lstrip('# ')+'（私有事件稿）','state':'private_draft','publication_scope':'private_research','text':text,'text_sha256':hashlib.sha256(payload[narrative_member]).hexdigest(),'paragraphs':paras,'publication':{'status':'private','auto_publish':False,'receipt':None},'reuses_claim_ids':[review_id(id) for id in dependencies]}, {},links=refs+[review_link(id) for id in dependencies]+[link('workflow-protocol','protocol/v1.1')]))
    if batch==163:
        concepts=json.loads(payload['可反驳解释与跨项目比较.json'])
        if any(c['causal_influence'] is not False for c in concepts['comparisons']):raise ValueError('comparison cannot establish influence')
        for c in concepts['concepts']:
            side.append(_record('workflow-concept',prefix+'/'+c['id'],{'title':c['interpretation'],'state':'hypothesis','interpretation':c['interpretation'],'alternative_explanation':c['alternative'],'falsifying_evidence_needed':c['falsification'],'support_anchors':c['supports'],'not_event_fact':True,'comparisons':concepts['comparisons']}, {},links=[evidence[id] for id in c['supports']]))
        public=json.loads(payload['发表交接.json'])
        if public['new_publication'] is not False or public['auto_publish'] is not False or public['approved_event_claims'] or public['approved_images'] or public['public_video_url'] is not None:raise ValueError('private continuation cannot publish')
    else:
        public=json.loads(payload['发表交接_保持私有.json']);needs=json.loads(payload['关闭源内分支后所需组织文献.json'])
        if public['auto_publish'] is not False or public['event_material_status']!='private' or public['approved_images'] or public['approved_video'] is not None or public['new_public_draft'] is not None or public['method_article_unchanged'] is not True or needs['next_video_automatic'] is not False or needs['private_chat_scan'] is not False:raise ValueError('closure must not execute or publish')
        needref=link('workflow-evidence-needs',prefix+'/organization-evidence');side.append(_record('workflow-evidence-needs',prefix+'/organization-evidence',{'title':'关闭源内分支后的组织文献需求','state':'unresolved','requirements':needs['items'],'auto_execute':False,'found':False}, {},links=[private,link('workflow-queue','research-162/queue/3')]))
        branch={'source_id':sid,'group_id':gid,'title':'源内寻找落实分支已关闭；项目问题未决','state':'closed_with_unresolved_questions','project_question_status':'unresolved','meeting_end_confirmed':False,'implementation_confirmed':False,'semantic_complete':False,'auto_execute':False,'next_evidence_id':needref['id'],'summary':closure,'next_evidence':needs['items']}
        side.append(_record('video-branch',sid+'/closure/164',branch,{},links=[link('video-group',gid),needref,private,link('workflow-queue','research-162/queue/1')]+[review_link(id) for id in dependencies]+[review_link(r['claim_id']) for r in rows]))
    side.append(_record('workflow-batch',prefix+'/'+sha,{'title':str(batch)+'同源有界研究','state':'imported','protocol_version':'v1.1','source_id':sid,'input_range':bounds,'new_confirmed_events':0,'new_sources':0,'auto_execute':False,'auto_publish':False,'publication_receipts':0,'research_manifest_sha256':sha}, {},links=[private,link('video-group',gid)]+refs+[review_link(id) for id in dependencies]))
