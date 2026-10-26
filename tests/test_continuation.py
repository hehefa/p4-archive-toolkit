"""Synthetic ordered same-source coverage; no original recordings or personal text."""
import copy,json,hashlib,tempfile,unittest
from pathlib import Path
from p4vault.core import canonical,file_hash,validate,connect
from p4vault.continuation import import_continuation,claim_labels,PROFILES
from p4vault.adjacent import import_adjacent
from p4vault.workflow import import_workflow,workflow_detail,workflow_list
from p4vault.identity import identity_id,review_id
from p4vault.review import review_detail
from p4vault.video import video_timeline,hms,frame_bytes
from p4vault.backup import backup,restore
from tests.test_workflow import workflow_fixture,csv_bytes
from tests.test_video import pack
from tests.test_identity import protected


def continuation_fixture(root):
    root=root.resolve();db,local,f,_=workflow_fixture(root);duration=1458.123333;s=f['来源身份与相邻依据.json'];s['duration']=duration;sid=s['source_id'];path=s['path'];group=s['event_candidate_id'];f['覆盖质量与停止依据.json']['unread']=[600,duration];f['同目录原片元数据.json'][1]['metadata']['format']['duration']=str(duration)
    for n in [2,3]:f['下一批覆盖队列.json'].append({'priority':n,'source_path':'unread-source-'+str(n),'range':'not read','question':'Untouched task '+str(n),'stop_rule':'Do not execute'})
    a=pack(root/'adjacent',f);import_adjacent(db,root/'adjacent',a,local);import_workflow(db,root/'adjacent',a,local);protocol=f['长期准则保存与读回核验.json']['sha256'];graph=f['原包与来源核验.json']['89_graph_sha256'];packages={}
    for batch in [163,164]:
        folder=root/str(batch);member,quality,index_member,source_member,narrative,csv_member=PROFILES[batch];files={};index=[];runs=[]
        specs=[(600,900,[(600,620),(880,900)]),(900,1200,[(925.38,940),(940,956.44),(1023.4,1039.4),(1039.4,1052.4),(1072.4,1082.4),(1082.4,1092.4),(1190,1200)])] if batch==163 else [(1200,duration,[(1200,1210),(1235.6,1246.6),(1246.6,1247.6),(1258.6,1281.8),(1310.22,1340.22),(1340.22,1360.08),(1420.52,1438.9),(1442.6,1447.6),(1450,1458.12)])]
        for start,end,parts in specs:
            name='window_'+str(start)+'_'+('1458.12' if batch==164 else str(end));audio=b'synthetic bounded clip';trans=[]
            for n,(x,y) in enumerate(parts,1):
                text='PRIVATE_SENTINEL synthetic local segment '+str(n);trans.append({'offsets':{'from':round((x-start)*1000),'to':round((y-start)*1000)},'text':text});index.append({'segment_id':str(start)+'-'+str(n),'start':x,'end':y,'start_hms':hms(x),'end_hms':hms(y),'speaker_id':None,('text_uncorrected' if batch==163 else 'machine_text_uncorrected'):text,**({'raw_json':str(folder/name/'machine.json'),'status':'unverified'} if batch==163 else {'source_id':sid,'private':True})})
            files[name+'/audio.wav']=audio;files[name+'/machine.json']={'transcription':trans};files[name+'/result.json']={'source':path,'start':start,'end':end,'human_listened':False,'exit_code':0,'source_stat_unchanged':True,'stop_reason':None,'audio_sha256':hashlib.sha256(audio).hexdigest()};runs.append({'start':start,'end':end,'segments':len(parts),'invalid_timing_segment_ids':[],'exit_code':0,'source_stat_unchanged':True})
        files[index_member]=index;frames=[660,840,1040,1060,1090] if batch==163 else [1260,1440]
        for t in frames:files['frame_'+str(t)+'.jpg']=b'\xff\xd8\xff\xe0synthetic\xff\xd9'
        common={'source_id':sid,'source_path':path,'event_candidate_id':group,'event_date':None,'person_id':None,'action_confirmed':False,'evidence_level':'machine_unverified_private','claim':'Synthetic bounded candidate','limits':'No implementation or identity proof'}
        windows=[[[925.38,956.44]],[[1072.4,1092.4]],[[1023.4,1052.4]],[[600,1200]]] if batch==163 else [[[1235.6,1247.6]],[[1258.6,1281.8],[1420.52,1438.9],[1442.6,1447.6]],[[1200,duration]]]
        rows=[];csv=[]
        for n,ws in enumerate(windows,1):
            id='NEW'+str(batch)+'-'+str(n).zfill(2);negative=n==len(windows);r={**common,'claim_id':id}
            if batch==163:r.update(start=ws[0][0],end=ws[0][1],start_hms=hms(ws[0][0]),end_hms=hms(ws[0][1]),title='Synthetic response '+str(n),speaker_position_id=None if negative else 'SP163-'+str(n).zfill(2),stage='bounded_negative' if negative else 'response_candidate',visibility='private')
            else:r.update(anchors=ws,type='bounded_negative_and_branch_closure' if negative else 'response_candidate')
            rows.append(r)
            for x,y in ws:csv.append({'claim_id':id,'source_path':path,'start_hms':hms(x),'end_hms':hms(y),'claim':r['claim'],'limits':r['limits']})
        files[csv_member]=csv_bytes(csv);files[narrative]='# Synthetic private continuation\n\nPrior context [NEW162-03].\n\n'+('\n\n'.join('Bounded evidence ['+r['claim_id']+'].' for r in rows))+('\n\nInherited responses [NEW163-01、02].' if batch==164 else '')
        d={'protocol_version':'v1.1','records':rows,'new_confirmed_events':0,'new_named_people':0,'auto_publish':False,'new_input_seconds':600 if batch==163 else duration-1200};files[member]=d
        if batch==163:
            d['reuses_claim_ids']=['NEW162-02','NEW162-03'];files[source_member]={'source_id':sid,'path':path,'sample_matches162':True,'size_matches162':True,'event_date':None,'new_start':600};files[quality]={'new_machine_input':[600,1200],'new_machine_text_reviewed':[600,1200],'prior_162':[0,600],'unread':[1200,duration],'human_listened_seconds':0,'continuous_video_viewed_seconds':0,'stills_viewed':frames,'runs':runs,'new_segments':len(index),'excluded_private_or_unreliable_ranges':[[600,925.38],[956.44,1023.4],[1052.4,1072.4],[1092.4,1200]],'exclusion_reason':'Private context not adopted'};files['旧包与来源核验.json']={'adjacent':{'mismatches':[]},'89_graph':graph,'protocol_v1.1_sha256':protocol};files['可反驳解释与跨项目比较.json']={'concepts':[{'id':'C163-01','interpretation':'Listening is not implementation','alternative':'ASR error','falsification':'Later action evidence','supports':['NEW163-02','NEW163-04']}],'comparisons':[{'causal_influence':False}]};files['发表交接.json']={'new_publication':False,'auto_publish':False,'approved_event_claims':[],'approved_images':[],'public_video_url':None}
        else:
            d.update(depends_on_claims=['NEW162-03','NEW163-02','NEW163-03','NEW163-04'],auto_run_next_video=False,source_branch_status='closed_with_unresolved_questions');files[source_member]={'source_id':sid,'path':path,'sample_match':True,'size_match':True,'bytes':Path(path).stat().st_size,'duration':duration,'previous_input':[0,1200]};files[quality]={'new_range':[1200,duration],'new_segments':len(index),'new_stills_viewed':frames,'human_listened':False,'continuous_video_viewed':False,'invalid_timing_segments':[],'excluded_segment_ids':['1200-5'],'coarse_question_segment_ids':['1200-2','1200-4']};files['89与151保持核验.json']={'89_graph':graph,'151_mismatches':[]};deps=[]
            for b,p,sha,delta in [(162,root/'adjacent',a,'delta_162_独立增量.json'),(163,root/'163',packages[163][1],PROFILES[163][0])]:deps.append({'batch':b,'package':str(p),'manifest_sha256':sha,'delta_path':str(p/delta),'delta_sha256':file_hash(p/delta)})
            files['来源依赖与旧包核验.json']={'previous_packages':deps,'protocol_sha256':protocol};previous=[{'batch':162,**r} for r in f['覆盖质量与停止依据.json']['runs']]+[{'batch':163,**r} for r in packages[163][0][PROFILES[163][1]]['runs']];files['162_163_164同源完整覆盖说明.json']={'source_id':sid,'container_duration':duration,'event_complete':False,'machine_input_uncovered':[],'human_listened_seconds':0,'continuous_video_viewed_seconds':0,'human_listening_uncovered':[0,duration],'quality_exclusions':[{'batch':164,'start':1310.22,'end':1340.22,'reason':'Distorted whole segment'},{'batch':164,'start':1340.22,'end':1360.08,'reason':'Unstable following text'}],'ranges':previous+[{'batch':164,**r} for r in runs],'total_machine_segments':sum(r['segments'] for r in previous+runs),'stills_in_this_source':[1,150,660,840,1040,1060,1090,1260,1440]};files['发表交接_保持私有.json']={'auto_publish':False,'event_material_status':'private','approved_images':[],'approved_video':None,'new_public_draft':None,'method_article_unchanged':True};files['关闭源内分支后所需组织文献.json']={'next_video_automatic':False,'private_chat_scan':False,'items':[{'question':'Synthetic organization question','needed':'Dated schedule or delivery','required_link':'Proposal to execution','not_sufficient':'Intent alone'}]}
        files[narrative]=files[narrative].encode()
        sha=pack(folder,files);packages[batch]=(files,sha)
    return db,local,packages


class ContinuationTest(unittest.TestCase):
    def test_order_same_source_history_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,p=continuation_fixture(root)
            try:
                before=protected(db);method=workflow_detail(db,identity_id('workflow-article','research-162/method-candidate'));gid=identity_id('video-group','synthetic-video-group/source/synthetic-adjacent');old=video_timeline(db,gid)['coverage'][0]
                with self.assertRaises(ValueError):import_continuation(db,root/'164',p[164][1],local)
                self.assertEqual(protected(db),before)
                for batch in [163,164]:self.assertEqual(import_continuation(db,root/str(batch),p[batch][1],local)['status'],'imported')
                for batch in [163,164]:self.assertEqual(import_continuation(db,root/str(batch),p[batch][1],local)['status'],'already_imported')
                t=video_timeline(db,gid);self.assertEqual(len(t['stages']),10);self.assertEqual(t['coverage'][0],old);self.assertTrue(t['input_complete']);self.assertEqual(t['machine_input_uncovered'],[]);self.assertFalse(t['semantic_complete']);self.assertEqual(len(t['frames']),9);self.assertEqual(t['branches'][0]['project_question_status'],'unresolved');self.assertEqual(workflow_detail(db,method['id']),method);self.assertTrue(validate(db)['ok'])
                for batch in [163,164]:
                    for r in p[batch][0][PROFILES[batch][0]]['records']:
                        b=review_detail(db,review_id(r['claim_id']))['body'];self.assertNotIn('PRIVATE_SENTINEL',canonical(b));self.assertEqual(b['mapping']['links'],[]);self.assertTrue(frame_bytes(db,local,b['video_context']['frame']['id']).startswith(b'\xff\xd8'))
            finally:db.close()

    def test_coarse_windows_exclusions_and_shorthand_navigation(self):
        self.assertEqual(claim_labels('[NEW163-01、02] and [NEW164-02]'),['NEW163-01','NEW163-02','NEW164-02'])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,p=continuation_fixture(root)
            try:
                for b in [163,164]:import_continuation(db,root/str(b),p[b][1],local)
                r=review_detail(db,review_id('NEW164-02'))['body'];self.assertEqual(len(r['video_context']['evidence_windows']),3);self.assertTrue(r['source_evidence']['lines'][0]['coarse_timing']);negative=review_detail(db,review_id('NEW164-03'))['body'];self.assertEqual(negative['source_evidence']['lines'],[]);self.assertEqual(negative['video_context']['excluded_ranges'][0]['start'],1310.22)
                article=workflow_detail(db,identity_id('workflow-article','research-164/private-narrative'));self.assertEqual(article['body']['record']['paragraphs'][-1]['review_ids'],[review_id('NEW163-01'),review_id('NEW163-02')]);self.assertEqual(db.execute("SELECT count(*) FROM job WHERE kind='bounded_research' AND state='pending'").fetchone()[0],3)
            finally:db.close()

    def test_rejects_index_csv_privacy_coverage_and_protocol_drift_before_write(self):
        for mode in ['index','csv','person','timing','publish','protocol']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);db,local,p=continuation_fixture(root);f=p[163][0]
                try:
                    if mode=='index':f[PROFILES[163][2]][0]['start']+=1
                    if mode=='csv':f[PROFILES[163][5]]=f[PROFILES[163][5]].replace(b'00:15:25.380',b'00:15:25.390')
                    if mode=='person':f[PROFILES[163][0]]['records'][0]['person_id']='named-person'
                    if mode=='timing':f[PROFILES[163][1]]['new_machine_input'][0]=601
                    if mode=='publish':f['发表交接.json']['auto_publish']=True
                    if mode=='protocol':f['旧包与来源核验.json']['protocol_v1.1_sha256']='0'*64
                    sha=pack(root/'163',f);before=protected(db)
                    with self.assertRaises(ValueError):import_continuation(db,root/'163',sha,local)
                    self.assertEqual(before,protected(db))
                finally:db.close()

    def test_closure_rejects_semantic_completion_or_adopted_distortion(self):
        for mode in ['complete','exclusion','claim','total']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);db,local,p=continuation_fixture(root)
                try:
                    import_continuation(db,root/'163',p[163][1],local);f=p[164][0];c=f['162_163_164同源完整覆盖说明.json']
                    if mode=='complete':c['event_complete']=True
                    if mode=='exclusion':c['quality_exclusions'][0]['end']=1320
                    if mode=='claim':f[PROFILES[164][0]]['records'][0]['anchors']=[[1310.22,1340.22]]
                    if mode=='total':c['total_machine_segments']+=1
                    sha=pack(root/'164',f);before=protected(db)
                    with self.assertRaises(ValueError):import_continuation(db,root/'164',sha,local)
                    self.assertEqual(before,protected(db))
                finally:db.close()

    def test_full_backup_restore_preserves_source_closure_and_private_paragraphs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,p=continuation_fixture(root);other=None
            try:
                for b in [163,164]:import_continuation(db,root/str(b),p[b][1],local)
                backup(db,local,root/'backup.zip');restore(root/'backup.zip',root/'restored');other=connect(root/'restored/vault.sqlite3');self.assertEqual(protected(db),protected(other));self.assertEqual(workflow_list(db),workflow_list(other));gid=identity_id('video-group','synthetic-video-group/source/synthetic-adjacent');self.assertEqual(video_timeline(db,gid),video_timeline(other,gid));self.assertTrue(validate(other)['ok'])
            finally:
                if other:other.close()
                db.close()
