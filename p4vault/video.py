"""Private source-zero video stages. No decoding, ASR, biometric matching or uploads."""
import hashlib
import json
import math
import re
from pathlib import Path
from .core import canonical,digest,stable_id,file_hash
from .review import _member_payloads
from .identity import _record,identity_id,link,review_link
from .archive import _protected,persist_archive


def finite(value):return type(value) in (int,float) and math.isfinite(value)
def close(a,b):return finite(a) and finite(b) and abs(a-b)<0.000001

def interval(start,end,duration):
    if not finite(start) or not finite(end) or not 0<=start<end<=duration:raise ValueError('video interval outside source')
    return [start,end]

def hms(value):
    ms=round(value*1000);return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}.{ms%1000:03}'

def verify_media(source):
    p=Path(source['path'])
    if not p.is_absolute() or p.is_symlink() or any(x in {'.codex','.ssh','.aws','.agents'} for x in p.parts) or p.suffix.lower() not in {'.mp4','.mov'}:raise ValueError('unsafe video source path')
    before=p.stat()
    if before.st_size!=source['size'] or before.st_mtime_ns!=source['mtime_ns'] or before.st_size<2*1024*1024:raise ValueError('video source stat changed')
    with p.open('rb') as f:first=f.read(1024*1024);f.seek(-1024*1024,2);last=f.read(1024*1024)
    after=p.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or hashlib.sha256(first+last).hexdigest()!=source['sample_sha256']:raise ValueError('video source sample changed')
    return {'hash_kind':'first_last_1MiB_sample_not_full_file','sample_sha256':source['sample_sha256'],'full_hash_verified':False,'source_stat_verified':True}

def segments(payload,root,source,duration):
    result=[];runs=[]
    for name in payload:
        if not name.endswith('/result.json'):continue
        run=json.loads(payload[name]);folder=Path(name).parent;machine=(folder/'machine.json').as_posix();audio=(folder/'audio.wav').as_posix();start,end=interval(run['start'],run['end'],duration)
        if run['source']!=source['path'] or run.get('human_listened') is not False or run.get('exit_code')!=0 or run.get('source_stat_unchanged') is not True or run.get('stop_reason') is not None:raise ValueError('video ASR run boundary invalid')
        if hashlib.sha256(payload[audio]).hexdigest()!=run['audio_sha256']:raise ValueError('video derived audio hash mismatch')
        transcript=json.loads(payload[machine])['transcription'];previous=start
        for index,t in enumerate(transcript,1):
            a=start+t['offsets']['from']/1000;b=start+t['offsets']['to']/1000
            interval(a,b,duration)
            if a<start-1e-6 or b>end+1e-6 or a<previous-1e-6:raise ValueError('video machine segment timing invalid')
            previous=a;result.append({'start':a,'end':b,'text':t['text'].strip(),'raw_json':str(root/machine),'raw_member':machine,'raw_segment_index':index,'raw_sha256':hashlib.sha256(payload[machine]).hexdigest()})
        runs.append({'start':start,'end':end,'segments':len(transcript)})
    runs.sort(key=lambda x:x['start']);result.sort(key=lambda x:(x['start'],x['end']))
    if not runs or any(not close(a['end'],b['start']) for a,b in zip(runs,runs[1:])):raise ValueError('video input coverage is not contiguous')
    return result,runs

def verify_index(index,raw,batch,source_id):
    if len(index)!=len(raw):raise ValueError('video timeline count mismatch')
    for item,expected in zip(index,raw):
        if not close(item['start'],expected['start']) or not close(item['end'],expected['end']) or item.get('human_listened') is not False:raise ValueError('video absolute timeline mismatch')
        if batch==160:
            if item['machine_text']!=expected['text'] or Path(item['source_transcript']).resolve()!=Path(expected['raw_json']).resolve() or item['source_id']!=source_id:raise ValueError('video timeline text/source mismatch')
        elif item['machine_text_uncorrected']!=expected['text'] or Path(item['raw_json']).resolve()!=Path(expected['raw_json']).resolve() or item.get('speaker_id') is not None or item['start_hms']!=hms(item['start']) or item['end_hms']!=hms(item['end']):raise ValueError('video timeline label or text mismatch')

def import_video(db,folder,manifest_sha,local_dir):
    root=Path(folder).resolve();payload,manifest_raw=_member_payloads(root,manifest_sha)
    if 'delta_160_新视频研究增量.json' in payload:
        batch=160;member='delta_160_新视频研究增量.json';d=json.loads(payload[member]);source=d['source'];rows=d['stage_records'];protected=d['protected_manifests'];base=d['base89_sha256']
        if d.get('schema')!='p4.private.video-event-increment/160' or d.get('visibility')!='local_private' or d.get('new_named_persons') or d.get('biometric_matches') or d.get('replaced_records') or d.get('whole_event_completed') is not False:raise ValueError('anonymous candidate video increment required')
        if source!=json.loads(payload['原片身份与覆盖.json']) or rows!=json.loads(payload['五阶段新窗口档案.json']):raise ValueError('video package member disagreement')
        quality=json.loads(payload['质量分段与身份限制.json']);excluded=d['excluded_windows'];coverage=source['semantic_coverage'];bounds=coverage['machine_input'];coarse=[]
        if excluded!=quality['excluded_source_windows'] or excluded!=coverage['excluded'] or source['human_listened_seconds']!=0 or source['continuous_video_viewed_seconds']!=0:raise ValueError('video reliability boundary disagreement')
        frame_specs=json.loads(payload['已看静帧.json']);frame_times=[f['source_seconds'] for f in frame_specs]
        if frame_times!=source['stills_viewed']:raise ValueError('video frame time disagreement')
        index_member='原片时轴_机稿未听校.json';old_raw=[];old_payload=None;old_root=None;old_sha=None;tail=None
    elif 'delta_161_独立研究增量.json' in payload:
        batch=161;member='delta_161_独立研究增量.json';d=json.loads(payload[member]);verify=json.loads(payload['最终核验.json']);old_root=Path(d['inherited_package']).resolve();old_sha=verify['old160_manifest_expected']
        if old_root.parent!=root.parent or d.get('private') is not True or d.get('new_event_count')!=0 or d.get('new_named_person_count')!=0 or d.get('preserve_original160') is not True:raise ValueError('private continuation with preserved baseline required')
        old_payload,_=_member_payloads(old_root,old_sha);old=json.loads(old_payload['delta_160_新视频研究增量.json']);source=old['source'];rows=d['rows'];protected={**old['protected_manifests'],old_root.name:old_sha};base=old['base89_sha256']
        check=json.loads(payload['来源复核.json'])
        if any(check[k]!=source[k] for k in ('source_id','path','size','mtime_ns','sample_sha256')) or check.get('matches160') is not True or check.get('full_hash') is not False or check.get('event_date') is not None:raise ValueError('video continuation source mismatch')
        quality=json.loads(payload['采用范围与失真边界.json']);coverage=json.loads(payload['运行与覆盖核验.json']);bounds=[quality['new_windows'][0][0],quality['new_windows'][-1][1]];coarse=quality['coarse_timing_ranges'];excluded=[{'start':r['range'][0],'end':r['range'][1],'reason':r['reason']} for r in quality['other_read_but_not_used_as_event_facts']];tail=coverage['tail_without_recognized_text']
        if coverage['human_listened_seconds']!=0 or coverage['continuous_video_viewed_seconds']!=0 or quality['human_listened'] is not False or quality['old160_exclusions']!=old['excluded_windows'] or verify['old160_manifest_actual']!=old_sha or verify['old160_mismatches']:raise ValueError('video continuation quality disagreement')
        if quality['adopted_process_candidates']!=[[r['start'],r['end']] for r in rows]:raise ValueError('video adopted stage ranges mismatch')
        frame_times=coverage['new_stills_viewed'];frame_specs=[{'source_seconds':s,'preview':str(root/f'frame_{s}.jpg')} for s in frame_times];index_member='整片机稿_原片绝对时轴_未听校.json';old_raw,_=segments(old_payload,old_root,source,source['duration_seconds'])
    else:raise ValueError('unsupported private video increment')
    dependencies=_protected(db,root,local_dir,{'base89_sha256':base,'protected_base_manifests':protected});sample=verify_media(source);duration=source['duration_seconds'];interval(0,duration,duration)
    raw,runs=segments(payload,root,source,duration);index=json.loads(payload[index_member]);verify_index(index,old_raw+raw,batch,source['source_id'])
    if not close(bounds[0],runs[0]['start']) or not close(bounds[1],runs[-1]['end']):raise ValueError('video declared input range mismatch')
    if batch==160:
        if quality['segments']!=len(raw) or d['new_semantic_input_seconds']!=bounds[1]-bounds[0] or quality['zero_duration']!=0 or quality['out_of_window']!=0:raise ValueError('video initial counts mismatch')
    else:
        if not close(bounds[0],old_raw[-1]['end']) or not close(bounds[1],duration) or coverage['machine_input_coverage']!=[0,duration] or not close(coverage['new_input_seconds'],bounds[1]-bounds[0]) or quality['new_windows']!=[[r['start'],r['end']] for r in runs] or not close(tail[0],raw[-1]['end']) or not close(tail[1],duration):raise ValueError('video continuation coverage mismatch')
    for e in excluded:interval(e['start'],e['end'],duration)
    for a,b in coarse:interval(a,b,duration)
    expected=[f'NEW{batch}-{i:02}' for i in range(1,6 if batch==160 else 7)]
    if [r['id'] for r in rows]!=expected or len({r['speaker_position_id'] for r in rows})!=len(rows):raise ValueError('video stage IDs invalid')
    family=rows[0]['source_family'];group=rows[0]['event_group_id'];sid=source['source_id']
    if batch==160 and d['source_family']!=family:raise ValueError('video source family mismatch')
    if batch==161 and any(r[k]!=old['stage_records'][0][k] for r in rows for k in ('source_family','event_group_id','source_id')):raise ValueError('video continuation group mismatch')
    source_ref=link('video-source',sid);family_ref=link('video-family',family);group_ref=link('video-group',group);coverage_key=sid+'/batch/'+str(batch);side=[];frames=[]
    for frame in frame_specs:
        seconds=frame['source_seconds'];p=Path(frame['preview']).resolve()
        if p.parent!=root or not finite(seconds) or not bounds[0]<=seconds<=bounds[1] or p.name not in payload:raise ValueError('video frame provenance invalid')
        data=payload[p.name]
        if len(data)>2*1024*1024 or not data.startswith(b'\xff\xd8\xff') or not data.endswith(b'\xff\xd9'):raise ValueError('video frame not a bounded JPEG')
        key=sid+'/frame/'+str(seconds);ref={'manifest_sha256':manifest_sha,'path':p.name,'sha256':hashlib.sha256(data).hexdigest(),'actual_frame_seconds':seconds};frames.append({'id':identity_id('video-frame',key),'seconds':seconds})
        side.append(_record('video-frame',key,{'source_id':sid,'group_id':group,'source_seconds':seconds,'source_path':source['path'],'purpose':'upstream sparse still; not continuous viewing, speech attribution or identity proof'}, {},links=[source_ref,group_ref,link('video-coverage',coverage_key)],preview_ref=ref))
    if len({f['seconds'] for f in frames})!=len(frames):raise ValueError('duplicate video frames')
    if batch==160:
        side.append(_record('video-source',sid,source,{},links=[family_ref,group_ref],sample_verification=sample))
        side.append(_record('video-family',family,{'source_id':sid,'group_id':group,'independent_source_families':1}, {},links=[source_ref,group_ref]))
        side.append(_record('video-group',group,{'source_id':sid,'source_family':family,'event_date':None,'event_status':'candidate','new_confirmed_events':0,'duration_seconds':duration}, {},links=[source_ref,family_ref]))
    coverage_record={'source_id':sid,'source_family':family,'group_id':group,'batch':batch,'input_range':bounds,'input_seconds':bounds[1]-bounds[0],'machine_segments':len(raw),'runs':runs,'excluded':excluded,'coarse_timing_ranges':coarse,'tail_without_recognized_text':tail,'human_listened_seconds':0,'continuous_video_viewed_seconds':0,'new_confirmed_events':0,'meeting_end_confirmed':False,'implementation_confirmed':False,'frame_ids':frames,'stage_ids':expected,'quality':quality,'whole_input_covered':batch==161,'original_media_copied':False}
    side.append(_record('video-coverage',coverage_key,coverage_record,{},links=[source_ref,family_ref,group_ref]+[review_link(id) for id in expected]+[{'id':f['id'],'kind':'video-frame','target':'identity'} for f in frames]))
    prepared=[];timeline_sha=hashlib.sha256(payload[index_member]).hexdigest()
    for row in rows:
        start,end=interval(row['source_start_seconds'] if batch==160 else row['start'],row['source_end_seconds'] if batch==160 else row['end'],duration)
        if start<bounds[0] or end>bounds[1] or any(row[k]!=v for k,v in [('source_id',sid),('source_family',family),('event_group_id',group)]) or row.get('person_id') is not None or row.get('event_date') is not None:raise ValueError('video stage scope or identity invalid')
        if (batch==160 and row['visibility']!='local_private') or (batch==161 and (row['private'] is not True or row['implemented'] is not False or row['source_path']!=source['path'] or row['start_hms']!=hms(start) or row['end_hms']!=hms(end))):raise ValueError('video stage date or implementation boundary invalid')
        selected=[]
        for t in raw:
            if t['end']<=start or t['start']>=end:continue
            exclusions=[e for e in excluded if e['start']<t['end'] and e['end']>t['start']]
            selected.append({**t,'location':hms(t['start'])+' → '+hms(t['end']),'use_status':'excluded' if exclusions else 'machine_candidate','exclusion_reasons':[e['reason'] for e in exclusions],'coarse_timing':any(a<t['end'] and b>t['start'] for a,b in coarse),'human_listened':False,'speaker_id':None})
        if not selected:raise ValueError('video stage lacks machine anchors')
        pos=row['speaker_position_id'];side.append(_record('video-position',pos,{'label':pos,'source_id':sid,'group_id':group,'canonical_person_id':None,'rule':'stage speech position only; not a unique person or reliable diarization'}, {},links=[source_ref,group_ref,review_link(row['id'])],global_person_created=False))
        nearest=min(frames,key=lambda f:abs(f['seconds']-(start+end)/2));frame={**nearest,'within_stage':start<=nearest['seconds']<=end}
        r={'id':row['id'],'origin':'video_stage_'+str(batch),'project':(re.findall(r'《([^》]+)》',source['path']) or ['Source video stages'])[0],'event':group,'people':[pos],'relation':'machine_video_stage_candidate','strength':'machine_unreviewed','claim':row['claim'] if batch==160 else row['observed_in_machine_text'],'source':str(root/index_member),'anchor':hms(start)+' → '+hms(end),'limit':row['limitations'] if batch==160 else row['limits'],'event_date':None,'event_time_note':row['date_hint'] if batch==160 else check['date_evidence'],'interview_date':None,'publication_date':None,'original_record':row,'verification_level':'machine_unreviewed_no_diarization'}
        context={'source_id':sid,'source_path':source['path'],'source_family':family,'group_id':group,'group_ref':group_ref['id'],'source_ref':source_ref['id'],'position_ref':identity_id('video-position',pos),'coverage_ref':identity_id('video-coverage',coverage_key),'source_start_seconds':start,'source_end_seconds':end,'duration_seconds':duration,'source_zero':'original media container zero; no cross-version mapping','sample_verification':sample,'frame':frame,'excluded_ranges':[e for e in excluded if e['start']<end and e['end']>start],'coarse_timing_ranges':[p for p in coarse if p[0]<end and p[1]>start],'new_confirmed_events':0,'human_listened_seconds':0,'continuous_video_viewed_seconds':0,'global_identity_confirmed':False,'implementation_confirmed':False,'meeting_end_confirmed':False}
        body={'record':r,'event_title':row['title'],'layer_schema':'p4.private.video-stages/'+str(batch),'review_only':True,'visibility':'local_private','event_is_base_graph_fact':False,'identity_policy':'Anonymous stage labels only; no voice/face/name identity links','event_model':'One candidate meeting group; stages are not separate events','mapping':{'links':[],'explicit_people':[],'unresolved':[{'reference':pos,'role':'stage_position','reason':'anonymous; no reliable diarization'}],'name_only_merge':False,'unmapped_people_labels_retained':[pos]},'source_evidence':{'kind':'machine_timeline','anchor':r['anchor'],'numbering':'original media absolute seconds; uncorrected machine segments','source_sha256':timeline_sha,'lines':selected},'source_nature':'机器文字语义候选；未听校','direct_dialogue_record':False,'both_sides_independently_confirmed':False,'direction':'unverified_machine_turns','research_interpretation':None if batch==160 else row['research_interpretation'],'video_context':context}
        prepared.append((stable_id('review','p4-private-people-review',row['id']),row['id'],body))
    receipt={'schema':'p4.private.video-stages/'+str(batch),'review_only':True,'manifest_sha256':manifest_sha,'data_member':member,'record_ids':[r[0] for r in prepared],'base_graph_sha256':base,'protected_packages':dependencies,'new_graph_events':0,'new_global_people':0,'source_sample':sample,'machine_segments':len(raw),'input_range':bounds}
    pid=stable_id('review-package','p4-private-video',manifest_sha)
    return persist_archive(db,root,manifest_sha,payload,manifest_raw,local_dir,pid,receipt,prepared,side,{'schema':receipt['schema'],'source_id':sid,'group_id':group,'protected_packages':dependencies},{'records':len(prepared),'identity_navigation_records':len(side),'new_machine_segments':len(raw),'input_seconds':bounds[1]-bounds[0],'registered_stills':len(frames),'new_graph_events':0,'new_global_people':0,'public_grants_added':0})


def video_timeline(db,id):
    group=db.execute("SELECT upstream_id,body FROM identity_record WHERE id=? AND kind='video-group'",(id,)).fetchone()
    if not group:raise ValueError('video group missing')
    base=json.loads(group['body'])['record'];coverage=[];stages=[];frames=[]
    for row in db.execute("SELECT id,body FROM identity_record WHERE kind='video-coverage'"):
        b=json.loads(row['body'])['record']
        if b['group_id']==group['upstream_id']:coverage.append({'id':row['id'],**b});frames+=b['frame_ids']
    for row in db.execute('SELECT id,upstream_id,body FROM review_record'):
        b=json.loads(row['body']);v=b.get('video_context')
        if v and v['group_id']==group['upstream_id']:stages.append({'id':row['id'],'upstream_id':row['upstream_id'],'title':b['event_title'],'record_type':b['record']['relation'],'anchor_mode':v.get('anchor_mode','interval'),'start':v['source_start_seconds'],'end':v['source_end_seconds'],'position':next(iter(b['record']['people']),'未归人')})
    coverage.sort(key=lambda x:x['input_range'][0]);stages.sort(key=lambda x:x['start']);frames.sort(key=lambda x:x['seconds'])
    complete=bool(coverage and coverage[0]['input_range'][0]==0 and close(coverage[-1]['input_range'][1],base['duration_seconds']) and all(close(a['input_range'][1],b['input_range'][0]) for a,b in zip(coverage,coverage[1:])))
    adjacent=[]
    for r in db.execute("SELECT id,body FROM identity_record WHERE kind='video-adjacency'"):
        b=json.loads(r['body'])
        if any(x['id']==id for x in b['links']):adjacent.append({'id':r['id'],'record':b['record'],'links':b['links']})
    end=0;unread=[]
    for c in coverage:
        start,stop=c['input_range']
        if start>end:unread.append([end,start])
        end=max(end,stop)
    if end<base['duration_seconds']:unread.append([end,base['duration_seconds']])
    branches=[{'id':r['id'],**json.loads(r['body'])['record']} for r in db.execute("SELECT id,body FROM identity_record WHERE kind='video-branch'") if json.loads(r['body'])['record']['group_id']==group['upstream_id']]
    return {'branches':branches,'machine_input_uncovered':unread,'semantic_complete':False,'adjacent':adjacent,'group_id':group['upstream_id'],'source':base,'coverage':coverage,'stages':stages,'frames':frames,'machine_segments':sum(c['machine_segments'] for c in coverage),'input_complete':complete,'meeting_end_confirmed':False,'human_listened_seconds':0,'continuous_video_viewed_seconds':0,'new_confirmed_events':0}


def frame_bytes(db,local_dir,id):
    row=db.execute("SELECT r.body,p.snapshot_path,p.manifest_sha256 FROM identity_record r JOIN identity_package p ON p.id=r.package_id WHERE r.id=? AND r.kind='video-frame'",(id,)).fetchone()
    if not row:raise ValueError('video frame missing')
    ref=json.loads(row['body'])['preview_ref'];base=(Path(local_dir)/'research_snapshots').resolve();folder=Path(local_dir)/row['snapshot_path'];rel=Path(ref['path']);path=folder/rel
    if rel.is_absolute() or '..' in rel.parts or path.is_symlink() or not path.resolve().is_relative_to(base) or path.suffix.lower() not in {'.jpg','.jpeg'} or path.stat().st_size>2*1024*1024 or ref['manifest_sha256']!=row['manifest_sha256']:raise ValueError('unsafe video frame path')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ref['sha256'] or not raw.startswith(b'\xff\xd8\xff') or not raw.endswith(b'\xff\xd9'):raise ValueError('video frame hash mismatch')
    return raw


def validate_video_context(db,body):
    c=body.get('video_context');errors=[]
    if not c:return errors
    try:
        if c.get('anchor_mode')=='static_frame':
            a,b,duration=c['source_start_seconds'],c['source_end_seconds'],c['duration_seconds']
            frame=db.execute("SELECT body FROM identity_record WHERE id=? AND kind='video-frame'",(c['frame']['id'],)).fetchone()
            actual=json.loads(frame[0])['record'] if frame else {}
            if not all(finite(x) for x in (a,b,duration)) or not 0<=a==b<=duration or not close(actual.get('source_seconds'),a) or actual.get('source_id')!=c['source_id'] or actual.get('group_id')!=c['group_id']:raise ValueError('unverified frame point')
        else:interval(c['source_start_seconds'],c['source_end_seconds'],c['duration_seconds'])
    except (ValueError,KeyError,TypeError):errors.append({'code':'video_stage_timing'})
    for ref in c.get('evidence_refs',[]):
        if not db.execute("SELECT 1 FROM identity_record WHERE id=? AND kind='workflow-evidence'",(ref.get('id'),)).fetchone():errors.append({'code':'video_evidence_navigation_broken'})
    if any(c.get(k) is not False for k in ('global_identity_confirmed','implementation_confirmed','meeting_end_confirmed')) or any(c.get(k)!=0 for k in ('new_confirmed_events','human_listened_seconds','continuous_video_viewed_seconds')) or body['mapping']['links'] or body['mapping']['explicit_people'] or body['record']['event_date'] is not None:errors.append({'code':'video_stage_overclaim'})
    for key in ('group_ref','source_ref','position_ref','coverage_ref'):
        if key=='position_ref' and c.get(key) is None:continue
        if not c.get(key) or not db.execute('SELECT 1 FROM identity_record WHERE id=?',(c[key],)).fetchone():errors.append({'code':'video_navigation_broken'})
    if not db.execute("SELECT 1 FROM identity_record WHERE id=? AND kind='video-frame'",(c['frame']['id'],)).fetchone():errors.append({'code':'video_frame_broken'})
    return errors
