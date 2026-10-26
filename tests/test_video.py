"""Synthetic video bytes and machine candidates; no real media or private data."""
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from p4vault.core import canonical,connect,file_hash,validate,digest
from p4vault.process import import_process
from p4vault.video import import_video,video_timeline,frame_bytes,hms
from p4vault.identity import identity_id
from p4vault.review import review_detail,review_search
from p4vault.backup import backup,restore
from p4vault.publish import public_projection
from p4vault.server import make_server
from tests.test_identity import protected
from tests.test_process import process_fixture


def pack(folder,files):
    folder.mkdir(parents=True,exist_ok=True)
    for name,value in files.items():
        p=folder/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(value if isinstance(value,bytes) else canonical(value).encode())
    (folder/'MANIFEST.json').write_text(canonical({name:file_hash(folder/name) for name in files}));return file_hash(folder/'MANIFEST.json')


def video_fixture(root):
    import hashlib
    db,local,pf,psha,_=process_fixture(root);import_process(db,root/'process',psha,local)
    media=root/'synthetic.mp4';media.write_bytes(b'A'*(1024*1024)+b'B'*(1024*1024));st=media.stat()
    source={'source_id':'synthetic-video','path':str(media),'size':st.st_size,'mtime_ns':st.st_mtime_ns,'duration_seconds':12,'sample_sha256':hashlib.sha256(media.read_bytes()).hexdigest(),'sample_method':'first/last 1MiB, not full file verification','human_listened_seconds':0,'continuous_video_viewed_seconds':0,'stills_viewed':[1,4],'semantic_coverage':{'machine_input':[0,6],'excluded':[{'start':0,'end':0.5,'reason':'unreliable name; never identify speaker'}],'unread_audio':[6,12]}}
    dependencies={**pf['delta_159_独立研究增量.json']['protected'],'process':psha}
    base=pf['delta_159_独立研究增量.json']['base89_sha256'];family='synthetic-video-family';group='synthetic-video-group';sid=source['source_id']
    rows160=[{'id':f'NEW160-{i+1:02}','source_id':sid,'source_family':family,'event_group_id':group,'title':'Synthetic stage '+str(i),'source_start_seconds':i,'source_end_seconds':i+1,'claim':'Machine candidate plan','speaker_position_id':'SP160-'+str(i),'person_id':None,'event_date':None,'date_hint':'Unverified filename only','limitations':'Not an implemented activity','visibility':'local_private'} for i in range(5)]
    d160={'schema':'p4.private.video-event-increment/160','source':source,'stage_records':rows160,'source_family':family,'protected_manifests':dependencies,'base89_sha256':base,'new_named_persons':[],'biometric_matches':[],'replaced_records':[],'whole_event_completed':False,'visibility':'local_private','excluded_windows':source['semantic_coverage']['excluded'],'new_semantic_input_seconds':6}
    f160={'delta_160_新视频研究增量.json':d160,'原片身份与覆盖.json':source,'五阶段新窗口档案.json':rows160,'质量分段与身份限制.json':{'segments':6,'zero_duration':0,'out_of_window':0,'excluded_source_windows':d160['excluded_windows']},'已看静帧.json':[{'source_seconds':n,'preview':str(root/'video160'/f'frame_{n}.jpg')} for n in [1,4]]}
    def raw_run(files,folder,start,end,batch):
        sub=f'window_{start}_{end}';times=[(i,min(i+1,11.9)) for i in range(start,end)];ts=[{'offsets':{'from':round((a-start)*1000),'to':round((b-start)*1000)},'text':'machine candidate '+str(a)} for a,b in times];audio=b'synthetic derived audio';files[sub+'/audio.wav']=audio;files[sub+'/machine.json']={'transcription':ts};files[sub+'/result.json']={'source':source['path'],'start':start,'end':end,'human_listened':False,'exit_code':0,'source_stat_unchanged':True,'stop_reason':None,'audio_sha256':hashlib.sha256(audio).hexdigest()}
        if batch==160:return [{'id':sub+'-'+str(i+1),'source_id':sid,'start':a,'end':b,'machine_text':ts[i]['text'],'source_transcript':str(folder/sub/'machine.json'),'human_listened':False} for i,(a,b) in enumerate(times)]
        return [{'segment_id':str(start)+'-'+str(i+1),'start':a,'end':b,'start_hms':hms(a),'end_hms':hms(b),'machine_text_uncorrected':ts[i]['text'],'raw_json':str(folder/sub/'machine.json'),'human_listened':False,'speaker_id':None} for i,(a,b) in enumerate(times)]
    index=raw_run(f160,root/'video160',0,3,160)+raw_run(f160,root/'video160',3,6,160);f160['原片时轴_机稿未听校.json']=index
    for n in [1,4]:f160[f'frame_{n}.jpg']=b'\xff\xd8\xff\xe0synthetic fixture\xff\xd9'
    sha160=pack(root/'video160',f160)
    rows161=[{'id':f'NEW161-{i-5:02}','source_id':sid,'source_family':family,'event_group_id':group,'source_path':source['path'],'title':'Continuation '+str(i),'start':i,'end':min(i+1,11.9),'start_hms':hms(i),'end_hms':hms(min(i+1,11.9)),'observed_in_machine_text':'Machine candidate without correction','research_interpretation':'A separate research inference','limits':'No known speaker or implementation','speaker_position_id':'POS161-'+str(i),'person_id':None,'event_date':None,'private':True,'implemented':False} for i in range(6,12)]
    f161={'delta_161_独立研究增量.json':{'private':True,'new_event_count':0,'new_named_person_count':0,'preserve_original160':True,'rows':rows161,'inherited_package':str(root/'video160')},'最终核验.json':{'old160_manifest_expected':sha160,'old160_manifest_actual':sha160,'old160_mismatches':[]},'来源复核.json':{k:source[k] for k in ('source_id','path','size','mtime_ns','sample_sha256')},'采用范围与失真边界.json':{'new_windows':[[6,9],[9,12]],'coarse_timing_ranges':[[9,10]],'other_read_but_not_used_as_event_facts':[{'range':[11.9,12],'reason':'No recognized text, not silence or end of meeting'}],'old160_exclusions':d160['excluded_windows'],'human_listened':False,'adopted_process_candidates':[[r['start'],r['end']] for r in rows161]},'运行与覆盖核验.json':{'new_stills_viewed':[7,10],'human_listened_seconds':0,'continuous_video_viewed_seconds':0,'tail_without_recognized_text':[11.9,12],'machine_input_coverage':[0,12],'new_input_seconds':6}}
    f161['来源复核.json'].update(matches160=True,full_hash=False,event_date=None,date_evidence='Unverified metadata only')
    prefix=[{'segment_id':r['id'],'start':r['start'],'end':r['end'],'start_hms':hms(r['start']),'end_hms':hms(r['end']),'machine_text_uncorrected':r['machine_text'],'raw_json':r['source_transcript'],'speaker_id':None,'human_listened':False} for r in index]
    f161['整片机稿_原片绝对时轴_未听校.json']=prefix+raw_run(f161,root/'video161',6,9,161)+raw_run(f161,root/'video161',9,12,161)
    for n in [7,10]:f161[f'frame_{n}.jpg']=b'\xff\xd8\xff\xe0synthetic fixture\xff\xd9'
    sha161=pack(root/'video161',f161);return db,local,f160,sha160,f161,sha161


class VideoTest(unittest.TestCase):
    def test_static_frame_point_requires_exact_registered_source_and_evidence_links(self):
        import copy
        from p4vault.video import validate_video_context
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,f,a,g,b=video_fixture(root)
            try:
                import_video(db,root/'video160',a,local)
                body=review_detail(db,review_search(db,query='NEW160-01')['records'][0]['id'])['body']
                point=copy.deepcopy(body);c=point['video_context'];c.update(anchor_mode='static_frame',source_start_seconds=1,source_end_seconds=1)
                c['frame']['id']=identity_id('video-frame','synthetic-video/frame/1')
                self.assertEqual(validate_video_context(db,point),[])
                for mode in ['interval','different_time','different_source','different_group','missing_frame','negative','nan','broken_evidence']:
                    with self.subTest(mode=mode):
                        bad=copy.deepcopy(point);v=bad['video_context']
                        if mode=='interval':v.pop('anchor_mode')
                        elif mode=='different_time':v.update(source_start_seconds=2,source_end_seconds=2)
                        elif mode=='different_source':v['source_id']='other'
                        elif mode=='different_group':v['group_id']='other'
                        elif mode=='missing_frame':v['frame']['id']='missing'
                        elif mode=='negative':v.update(source_start_seconds=-1,source_end_seconds=-1)
                        elif mode=='nan':v.update(source_start_seconds=float('nan'),source_end_seconds=float('nan'))
                        else:v['evidence_refs']=[{'id':'missing'}]
                        self.assertTrue(validate_video_context(db,bad))
            finally:db.close()

    def test_sequential_stages_preserve_prior_history_and_anonymous_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,f,a,g,b=video_fixture(root)
            try:
                before=protected(db);old_identity=[tuple(r) for r in db.execute('SELECT * FROM identity_record ORDER BY rowid')]
                self.assertEqual(import_video(db,root/'video160',a,local)['records'],5);first=protected(db)
                self.assertFalse(video_timeline(db,identity_id('video-group','synthetic-video-group'))['input_complete'])
                self.assertEqual(import_video(db,root/'video161',b,local)['records'],6)
                for old in (before,first):
                    for table,rows in old.items():self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')][:len(rows)],rows)
                self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM identity_record ORDER BY rowid')][:len(old_identity)],old_identity)
                t=video_timeline(db,identity_id('video-group','synthetic-video-group'));self.assertEqual((len(t['stages']),len(t['frames']),t['machine_segments']),(11,4,12));self.assertTrue(t['input_complete']);self.assertFalse(t['meeting_end_confirmed']);self.assertEqual(t['new_confirmed_events'],0)
                r=review_detail(db,review_search(db,query='NEW161-04')['records'][0]['id'])['body'];self.assertNotEqual(r['research_interpretation'],r['record']['claim']);self.assertTrue(r['source_evidence']['lines'][0]['coarse_timing']);self.assertEqual(r['mapping']['links'],[]);self.assertIsNone(r['record']['event_date']);self.assertFalse(r['video_context']['sample_verification']['full_hash_verified'])
                first=review_detail(db,review_search(db,query='NEW160-01')['records'][0]['id'])['body'];self.assertEqual(first['source_evidence']['lines'][0]['use_status'],'excluded')
                for name,sha in [('video160',a),('video161',b)]:self.assertEqual(import_video(db,root/name,sha,local)['status'],'already_imported')
                self.assertTrue(validate(db)['ok']);self.assertEqual(public_projection(db,'github-index')['count'],0)
            finally:db.close()

    def test_continuation_rejects_missing_dependency_or_changed_identity_time_source(self):
        for mode in ['dependency','identity','date','implementation','group','absolute_time','raw_text','prefix','coverage','source','frame','coarse']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);db,local,f,a,g,b=video_fixture(root)
                try:
                    if mode!='dependency':import_video(db,root/'video160',a,local)
                    row=g['delta_161_独立研究增量.json']['rows'][0]
                    if mode=='identity':row['person_id']='person'
                    elif mode=='date':row['event_date']='2023-02-19'
                    elif mode=='implementation':row['implemented']=True
                    elif mode=='group':row['event_group_id']='new-event'
                    elif mode=='absolute_time':g['整片机稿_原片绝对时轴_未听校.json'][6]['start']=0
                    elif mode=='raw_text':g['整片机稿_原片绝对时轴_未听校.json'][6]['machine_text_uncorrected']='corrected without evidence'
                    elif mode=='prefix':g['整片机稿_原片绝对时轴_未听校.json'][0]['machine_text_uncorrected']='rewritten prior text'
                    elif mode=='coverage':g['运行与覆盖核验.json']['new_input_seconds']=99
                    elif mode=='source':g['来源复核.json']['sample_sha256']='0'*64
                    elif mode=='frame':g['frame_7.jpg']=b'not JPEG'
                    elif mode=='coarse':g['采用范围与失真边界.json']['coarse_timing_ranges']=[[10,9]]
                    b=pack(root/'video161',g);before=protected(db);count=db.execute('SELECT count(*) FROM identity_record').fetchone()[0]
                    with self.assertRaises(ValueError):import_video(db,root/'video161',b,local)
                    self.assertEqual(before,protected(db));self.assertEqual(db.execute('SELECT count(*) FROM identity_record').fetchone()[0],count)
                finally:db.close()

    def test_initial_source_sample_and_late_identity_collision_roll_back(self):
        for mode in ['sample','collision']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);db,local,f,a,g,b=video_fixture(root)
                try:
                    if mode=='sample':
                        f['delta_160_新视频研究增量.json']['source']['sample_sha256']='0'*64;a=pack(root/'video160',f)
                    else:
                        from p4vault.identity import _record,_persist
                        from p4vault.review import _member_payloads
                        sha=pack(root/'collision',{'synthetic.json':{'synthetic':True}});payload,manifest=_member_payloads(root/'collision',sha);_persist(db,root/'collision',sha,payload,manifest,local,[_record('video-position','SP160-0',{'label':'preexisting unrelated position'},{})],{'schema':'synthetic'})
                    before=protected(db)
                    with self.assertRaises(ValueError):import_video(db,root/'video160',a,local)
                    self.assertEqual(before,protected(db))
                finally:db.close()

    def test_backup_restore_and_protected_frame_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,f,a,g,b=video_fixture(root)
            try:
                import_video(db,root/'video160',a,local);import_video(db,root/'video161',b,local);backup(db,local,root/'backup.zip');restore(root/'backup.zip',root/'restored');other=connect(root/'restored/vault.sqlite3')
                try:
                    id=identity_id('video-group','synthetic-video-group');self.assertEqual(video_timeline(db,id),video_timeline(other,id));self.assertTrue(validate(other)['ok'])
                    frame=identity_id('video-frame','synthetic-video/frame/7');self.assertEqual(frame_bytes(db,local,frame),frame_bytes(other,root/'restored',frame))
                finally:other.close()
                server=make_server(local/'vault.sqlite3',local,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(server.server_address[1])
                try:
                    self.assertEqual(urllib.request.urlopen(url+'/api/review-frame?id='+frame).read(),g['frame_7.jpg'])
                    self.assertEqual(json.load(urllib.request.urlopen(url+'/api/video-timeline?id='+id))['machine_segments'],12)
                    with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(urllib.request.Request(url+'/api/review-frame?id='+frame,headers={'Origin':'https://example.invalid'}))
                    self.assertEqual(e.exception.code,403);e.exception.close()
                    with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(url+'/api/review-frame?id=../../synthetic.mp4')
                    e.exception.close()
                    frozen=local/'research_snapshots'/('identity-'+b)/'frame_7.jpg';frozen.write_bytes(b'changed')
                    with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(url+'/api/review-frame?id='+frame)
                    self.assertEqual(e.exception.code,400);e.exception.close()
                    frozen.unlink()
                    with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(url+'/api/review-frame?id='+frame)
                    self.assertEqual(e.exception.code,400);e.exception.close()
                finally:server.shutdown();thread.join();server.server_close()
            finally:db.close()

    def test_validation_rejects_promoted_event_or_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,f,a,g,b=video_fixture(root)
            try:
                import_video(db,root/'video160',a,local);id=review_search(db,query='NEW160-01')['records'][0]['id'];body=review_detail(db,id)['body'];body['video_context']['meeting_end_confirmed']=True
                with db:db.execute('UPDATE review_record SET body=?,digest=? WHERE id=?',(canonical(body),digest(body),id))
                self.assertFalse(validate(db)['ok'])
                with self.assertRaises(ValueError):public_projection(db,'github-index')
            finally:db.close()
