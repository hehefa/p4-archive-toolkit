"""Bounded adjacent-source research; preserve independent clocks and excluded timing."""
import csv,io,json,re,hashlib
from datetime import datetime
from pathlib import Path
from .core import digest,stable_id
from .review import _member_payloads
from .identity import _record,identity_id,link,review_link
from .archive import _protected,persist_archive
from .video import verify_media,interval,close,hms


def read_machine(payload,root,source,quality):
    result=[];flags=[]
    for run in quality['runs']:
        name=f"window_{run['start']}_{run['end']}";receipt=json.loads(payload[name+'/result.json']);ts=json.loads(payload[name+'/machine.json'])['transcription']
        if receipt['source']!=source['path'] or receipt['start']!=run['start'] or receipt['end']!=run['end'] or receipt['human_listened'] is not False or receipt['exit_code']!=0 or receipt['source_stat_unchanged'] is not True or len(ts)!=run['segments'] or hashlib.sha256(payload[name+'/audio.wav']).hexdigest()!=receipt['audio_sha256']:raise ValueError('adjacent run mismatch')
        actual=[]
        for n,t in enumerate(ts,1):
            a=run['start']+t['offsets']['from']/1000;b=run['start']+t['offsets']['to']/1000;interval(a,b,source['duration_seconds']);bad=a<run['start'] or b>run['end']
            if bad:actual.append({'segment':n,'start':a,'end':b})
            result.append({'start':a,'end':b,'text':t['text'].strip(),'raw_json':str(root/name/'machine.json'),'raw_segment_index':n,'timing_invalid':bad,'human_listened':False})
        if actual!=run['timing_flags']:raise ValueError('undeclared adjacent timing exception')
        flags+=actual
    index=json.loads(payload['机稿原片时轴_未听校_私有.json'])
    if len(index)!=len(result):raise ValueError('adjacent index count mismatch')
    for a,b in zip(index,result):
        if not close(a['start'],b['start']) or not close(a['end'],b['end']) or a['machine_text']!=b['text'] or Path(a['raw_json']).resolve()!=Path(b['raw_json']).resolve() or a['source_id']!=source['source_id'] or a['human_listened'] is not False or a['speaker_id'] is not None:raise ValueError('adjacent absolute index mismatch')
    if any(not any(close(f['start'],e['range'][0]) and close(f['end'],e['range'][1]) for e in quality['excluded']) for f in flags):raise ValueError('timing exception must be excluded')
    return result,flags


def import_adjacent(db,folder,sha,local_dir):
    root=Path(folder).resolve();payload,manifest=_member_payloads(root,sha);d=json.loads(payload['delta_162_独立增量.json']);s=json.loads(payload['来源身份与相邻依据.json']);quality=json.loads(payload['覆盖质量与停止依据.json']);baseline=json.loads(payload['原包与来源核验.json'])
    if d['new_confirmed_event_count']!=0 or d['new_confirmed_named_person_count']!=0 or d['auto_publish'] is not False or d['reuses_packages']!=[160,161] or s['seamless_confirmed'] is not False or s['shared_global_timeline'] is not False or s['public_url'] is not None or s['full_hash'] is not False or s['event_date'] is not None:raise ValueError('adjacent candidate/private boundaries required')
    protected={}
    for name,value in baseline.items():
        if name=='89_graph_sha256':continue
        if value['mismatches']:raise ValueError('adjacent protected mismatch')
        from .core import file_hash
        protected[name]=file_hash(root.parent/name/'MANIFEST.json')
    if len(protected)!=2:raise ValueError('two prior video packages required')
    deps=_protected(db,root,local_dir,{'base89_sha256':baseline['89_graph_sha256'],'protected_base_manifests':protected})
    old=db.execute("SELECT body FROM identity_record WHERE kind='video-source' AND upstream_id=?",(s['prior_source_id'],)).fetchone()
    if not old:raise ValueError('adjacent prior source missing')
    old=json.loads(old[0])['record'];source={'source_id':s['source_id'],'path':s['path'],'size':s['bytes'],'mtime_ns':s['mtime_ns'],'sample_sha256':s['head_tail_1MiB_sha256'],'duration_seconds':s['duration']};sample=verify_media(source);verify_media(old)
    meta={r['path']:r['metadata']['format'] for r in json.loads(payload['同目录原片元数据.json'])};a=meta[old['path']];b=meta[source['path']]
    gap=(datetime.fromisoformat(b['tags']['creation_time'])-datetime.fromisoformat(a['tags']['creation_time'])).total_seconds()-float(a['duration'])
    if not close(float(a['duration']),old['duration_seconds']) or not close(float(b['duration']),source['duration_seconds']) or not close(gap,s['conditional_gap_seconds']) or not s['conditions']:raise ValueError('adjacent conditional timing mismatch')
    if quality['human_listened_seconds']!=0 or quality['continuous_video_viewed_seconds']!=0 or quality['new_input']!=[0,600] or quality['unread']!=[600,source['duration_seconds']]:raise ValueError('adjacent bounded coverage mismatch')
    raw,flags=read_machine(payload,root,source,quality);rows=d['records']
    if [r['id'] for r in rows]!=['NEW162-01','NEW162-02','NEW162-03']:raise ValueError('adjacent claim IDs invalid')
    sid=source['source_id'];candidate=s['event_candidate_id'];group=candidate+'/source/'+sid;family=sid+'/candidate-family';coverage_key=sid+'/batch/162';oldgroup=candidate;adjkey=s['prior_source_id']+'/candidate-next/'+sid;side=[];frames=[];boundary=[]
    for frame in quality['stills_viewed']:
        is_old=frame['file']==Path(old['path']).name;fsid=old['source_id'] if is_old else sid;fg=oldgroup if is_old else group;seconds=frame['time'];member=frame['file']+'_'+str(seconds)+'.jpg'
        if frame['file'] not in {Path(old['path']).name,Path(source['path']).name} or not 0<=seconds<=(old if is_old else source)['duration_seconds']:raise ValueError('adjacent frame source mismatch')
        image=payload[member]
        if len(image)>2*1024*1024 or not image.startswith(b'\xff\xd8\xff') or not image.endswith(b'\xff\xd9'):raise ValueError('invalid adjacent frame')
        key=fsid+'/frame/'+str(seconds);ref={'id':identity_id('video-frame',key),'seconds':seconds,'source_id':fsid};boundary.append(ref)
        if not is_old:frames.append(ref)
        side.append(_record('video-frame',key,{'source_id':fsid,'group_id':fg,'source_seconds':seconds,'source_path':(old if is_old else source)['path'],'purpose':'upstream boundary still; no identities or continuous viewing'}, {},links=[link('video-source',fsid),link('video-group',fg),link('video-adjacency',adjkey)],preview_ref={'manifest_sha256':sha,'path':member,'sha256':hashlib.sha256(image).hexdigest(),'actual_frame_seconds':seconds}))
    side.append(_record('video-source',sid,source,{},links=[link('video-family',family),link('video-group',group),link('video-adjacency',adjkey)],sample_verification=sample))
    side.append(_record('video-family',family,{'source_id':sid,'group_id':group,'candidate_event_id':candidate,'membership_of_prior_family':'unconfirmed'}, {},links=[link('video-source',sid),link('video-adjacency',adjkey)]))
    side.append(_record('video-group',group,{'source_id':sid,'source_family':family,'event_candidate_id':candidate,'event_date':None,'event_status':'candidate','new_confirmed_events':0,'duration_seconds':source['duration_seconds']}, {},links=[link('video-source',sid),link('video-adjacency',adjkey)]))
    side.append(_record('video-adjacency',adjkey,s,{},links=[link('video-group',oldgroup),link('video-group',group)]+[{'id':f['id'],'kind':'video-frame','target':'identity'} for f in boundary],boundary_frames=boundary))
    coverage={'source_id':sid,'source_family':family,'group_id':group,'batch':162,'input_range':[0,600],'input_seconds':600,'machine_segments':len(raw),'runs':quality['runs'],'excluded':[{'start':r['range'][0],'end':r['range'][1],'reason':r['reason']} for r in quality['excluded']],'coarse_timing_ranges':[],'tail_without_recognized_text':None,'human_listened_seconds':0,'continuous_video_viewed_seconds':0,'new_confirmed_events':0,'meeting_end_confirmed':False,'implementation_confirmed':False,'frame_ids':frames,'boundary_frames':boundary,'stage_ids':[r['id'] for r in rows],'quality':quality,'whole_input_covered':False,'unread':quality['unread'],'timing_flags':flags,'original_media_copied':False}
    side.append(_record('video-coverage',coverage_key,coverage,{},links=[link('video-source',sid),link('video-group',group)]+[review_link(r['id']) for r in rows]))
    prepared=[];index_member='机稿原片时轴_未听校_私有.json';indexsha=hashlib.sha256(payload[index_member]).hexdigest();csvrows=list(csv.DictReader(io.StringIO(payload['文章逐段证据表.csv'].decode('utf-8-sig'))));work_windows=[]
    for anchor in ('B03','B04'):
        loc=next(r['location'] for r in csvrows if r['anchor']==anchor);a,b=loc.split('–');
        def sec(v):h,m,s=v.split(':');return int(h)*3600+int(m)*60+float(s)
        work_windows.append([sec(a),sec(b)])
    for row in rows:
        interval(row['start'],row['end'],600)
        if row['private'] is not True or row['person_id'] is not None or row['action_implemented'] is not None or row['source_id']!=sid or row['source_path']!=source['path'] or row['event_candidate_id']!=candidate:raise ValueError('adjacent claim scope mismatch')
        windows=work_windows if row['type']=='process_candidate' else []
        selected=[{**r,'location':hms(r['start'])+' → '+hms(r['end']),'use_status':'machine_candidate','speaker_id':None} for r in raw if not r['timing_invalid'] and any(a<=r['start'] and r['end']<=b+1e-6 for a,b in windows)]
        if windows and not selected:raise ValueError('adjacent bounded anchors missing')
        nearest=min(frames,key=lambda f:abs(f['seconds']-(row['start']+row['end'])/2))
        context={'source_id':sid,'source_path':source['path'],'source_family':family,'group_id':group,'event_candidate_id':candidate,'group_ref':identity_id('video-group',group),'source_ref':identity_id('video-source',sid),'position_ref':None,'coverage_ref':identity_id('video-coverage',coverage_key),'source_start_seconds':row['start'],'source_end_seconds':row['end'],'duration_seconds':source['duration_seconds'],'source_zero':'independent original media clock; no concatenated timeline','sample_verification':sample,'frame':{**nearest,'within_stage':any(a<=nearest['seconds']<=b for a,b in windows) if windows else row['start']<=nearest['seconds']<=row['end']},'excluded_ranges':coverage['excluded'],'coarse_timing_ranges':[],'evidence_windows':windows,'bounded_finding':row['type']=='bounded_negative_finding','new_confirmed_events':0,'human_listened_seconds':0,'continuous_video_viewed_seconds':0,'global_identity_confirmed':False,'implementation_confirmed':False,'meeting_end_confirmed':False}
        record={'id':row['id'],'origin':'adjacent_video_162','project':(re.findall(r'《([^》]+)》',source['path']) or ['Source video study'])[0],'event':candidate,'people':[],'relation':row['type'],'strength':row['certainty'],'claim':row['statement'],'source':str(root/index_member),'anchor':hms(row['start'])+' → '+hms(row['end']),'limit':'Only the bounded reviewed machine text; no verified speaker, implementation or seamless continuation. Private background is not extracted.','event_date':None,'event_time_note':'Metadata time is conditional; source clocks remain independent','interview_date':None,'publication_date':None,'original_record':row,'verification_level':'machine_unreviewed_no_diarization'}
        body={'record':record,'event_title':row['statement'],'layer_schema':'p4.private.adjacent-research/162','review_only':True,'visibility':'local_private','event_is_base_graph_fact':False,'identity_policy':'No named or biometric identities','event_model':'Same event candidate, separate source clock; no new confirmed event','mapping':{'links':[],'explicit_people':[],'unresolved':[],'name_only_merge':False,'unmapped_people_labels_retained':[]},'source_evidence':{'kind':'machine_timeline','anchor':record['anchor'],'numbering':'independent original media seconds; only selected work questions displayed','source_sha256':indexsha,'lines':selected},'source_nature':'限定范围机稿候选或负结果；未听校','direct_dialogue_record':False,'both_sides_independently_confirmed':False,'direction':'unverified_machine_turns','video_context':context}
        prepared.append((stable_id('review','p4-private-people-review',row['id']),row['id'],body))
    receipt={'schema':'p4.private.adjacent-research/162','review_only':True,'data_member':'delta_162_独立增量.json','manifest_sha256':sha,'record_ids':[r[0] for r in prepared],'protected_packages':deps,'new_graph_events':0,'new_global_people':0,'source_sample':sample,'timing_exceptions_preserved':flags}
    return persist_archive(db,root,sha,payload,manifest,local_dir,stable_id('review-package','p4-private-adjacent',sha),receipt,prepared,side,{'schema':receipt['schema'],'protected_packages':deps},{'records':len(prepared),'identity_navigation_records':len(side),'machine_segments':len(raw),'registered_stills':len(boundary),'timing_exceptions':len(flags),'new_graph_events':0,'new_global_people':0,'public_grants_added':0})
