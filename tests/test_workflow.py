"""Synthetic method, adjacent media and publication receipts only."""
import csv,io,json,hashlib,tempfile,unittest
from pathlib import Path
from p4vault.core import canonical,connect,file_hash,validate
from p4vault.video import import_video,video_timeline
from p4vault.adjacent import import_adjacent
from p4vault.workflow import import_workflow,workflow_list,workflow_detail,record_action,import_receipt
from p4vault.identity import identity_id
from p4vault.review import review_search,review_detail
from p4vault.publish import public_projection
from p4vault.backup import backup,restore
from tests.test_video import video_fixture,pack
from tests.test_identity import protected


def csv_bytes(rows):
    out=io.StringIO();w=csv.DictWriter(out,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows);return ('\ufeff'+out.getvalue()).encode()


def workflow_fixture(root):
    root=root.resolve();db,local,f,a,g,b=video_fixture(root);import_video(db,root/'video160',a,local);import_video(db,root/'video161',b,local)
    old=f['delta_160_新视频研究增量.json']['source'];media=root/'adjacent.mp4';media.write_bytes(b'C'*(2*1024*1024));st=media.stat();sid='synthetic-adjacent';candidate='synthetic-video-group';folder=root/'adjacent'
    source={'source_id':sid,'path':str(media),'bytes':st.st_size,'mtime_ns':st.st_mtime_ns,'head_tail_1MiB_sha256':hashlib.sha256(media.read_bytes()).hexdigest(),'full_hash':False,'duration':610,'event_date':None,'prior_source_id':old['source_id'],'event_candidate_id':candidate,'relationship':'same_event_continuation_candidate','conditional_gap_seconds':1,'conditions':'start timestamp and same clock are assumed, not calibrated','seamless_confirmed':False,'shared_global_timeline':False,'public_url':None,'public_status':'unknown'}
    types=['source_relationship','process_candidate','bounded_negative_finding'];windows=[[0,49.3],[128.5,534.38],[0,600]];rows=[{'id':f'NEW162-{i+1:02}','type':kind,'statement':'Synthetic limited finding '+str(i),'start':a,'end':b,'certainty':'candidate','source_id':sid,'source_path':str(media),'event_candidate_id':candidate,'person_id':None,'private':True,'action_implemented':None} for i,(kind,(a,b)) in enumerate(zip(types,windows))]
    quality={'new_input':[0,600],'unread':[600,610],'runs':[],'stills_viewed':[{'file':Path(old['path']).name,'time':11},{'file':media.name,'time':1},{'file':media.name,'time':150}],'human_listened_seconds':0,'continuous_video_viewed_seconds':0,'excluded':[{'range':[0,30],'reason':'repetition/private material omitted'},{'range':[296.1,300.1],'reason':'0.1 seconds outside input; excluded, not repaired'}],'not_adopted':'personal biography not adopted','stop_reason':'stop after bounded work questions remain unanswered'}
    files={'delta_162_独立增量.json':{'records':rows,'reuses_packages':[160,161],'new_confirmed_event_count':0,'new_confirmed_named_person_count':0,'auto_publish':False},'来源身份与相邻依据.json':source,'覆盖质量与停止依据.json':quality,'原包与来源核验.json':{'video160':{'mismatches':[]},'video161':{'mismatches':[]},'89_graph_sha256':f['delta_160_新视频研究增量.json']['base89_sha256']},'同目录原片元数据.json':[{'path':old['path'],'metadata':{'format':{'duration':'12','tags':{'creation_time':'2020-01-01T00:00:00Z'}}}},{'path':str(media),'metadata':{'format':{'duration':'610','tags':{'creation_time':'2020-01-01T00:00:13Z'}}}}]};index=[]
    for start,end,parts in [(0,300,[(0,30,'PRIVATE_UNSELECTED_SENTINEL'),(128.5,136.5,'A bounded work question'),(296.1,300.1,'PRIVATE_OUTSIDE_INPUT_SENTINEL')]),(300,600,[(522.16,534.38,'Another bounded question')])]:
        name=f'window_{start}_{end}';audio=b'synthetic clip';ts=[{'offsets':{'from':round((a-start)*1000),'to':round((b-start)*1000)},'text':text} for a,b,text in parts];files[name+'/audio.wav']=audio;files[name+'/machine.json']={'transcription':ts};files[name+'/result.json']={'source':str(media),'start':start,'end':end,'human_listened':False,'exit_code':0,'source_stat_unchanged':True,'audio_sha256':hashlib.sha256(audio).hexdigest()};quality['runs'].append({'start':start,'end':end,'segments':len(parts),'timing_flags':[{'segment':3,'start':296.1,'end':300.1}] if start==0 else []})
        index += [{'source_id':sid,'start':a,'end':b,'machine_text':text,'human_listened':False,'speaker_id':None,'raw_json':str(folder/name/'machine.json')} for a,b,text in parts]
    files['机稿原片时轴_未听校_私有.json']=index
    for row in quality['stills_viewed']:files[row['file']+'_'+str(row['time'])+'.jpg']=b'\xff\xd8\xff\xe0synthetic\xff\xd9'
    protocol=root/'protocol.md';protocol.write_text('# Synthetic protocol v1.1\nNo automatic research or publication.');versioned=root/'protocol-v1.1.md';versioned.write_bytes(protocol.read_bytes());registry=root/'registry.csv';registry.write_bytes(csv_bytes([{'project_key':'same','title':'Same title','namespace':ns,'registry_key':prefix+':same','protocol_version':'v1.1','status':'unreviewed','cross_index_identity':'not merged'} for ns,prefix in [('artist_archive','artist'),('b_public_library','b')]]))
    files['长期准则保存与读回核验.json']={'version':'v1.1','fixed_path':str(protocol),'versioned_path':str(versioned),'sha256':file_hash(protocol),'registry':str(registry),'registry_sha256':file_hash(registry),'artist_index_entries':1,'b_index_entries':1,'total_namespaced_entries':2,'not_distinct_project_count':True};files['每批事件研究模板.json']={'protocol_version':'v1.1','concepts':[{'interpretation':'','alternative_explanation':'','falsifying_evidence_needed':''}]}
    text=root/'public-plan.md';text.write_text('A published invitation is only a plan.\n');anchors=[{'anchor':'D01','source':str(text),'location':'L1','level':'public plan','claim':'plan only','public_review':'needs editorial review'}]
    for anchor,sourcepath,loc in [('A04',old['path'],'00:00:09–00:00:10'),('A05',old['path'],'00:00:10–00:00:11'),('B01',str(media),'boundary metadata'),('B02',str(media),'00:00:00–00:00:49.300'),('B03',str(media),'00:02:08.500–00:02:16.500'),('B04',str(media),'00:08:42.160–00:08:54.380')]:anchors.append({'anchor':anchor,'source':sourcepath,'location':loc,'level':'machine candidate','claim':'source-bound statement','public_review':'private'})
    files['文章逐段证据表.csv']=csv_bytes(anchors);files['完整事件叙述_从召集到尚未定型的共同工作_私有.md']='# Synthetic narrative\n\nA plan [D01].\n\nA proposed process [A04、A05].\n\n据此提出一种解释，仍为方案。如果后续只有固定采访，应修订为未实施。\n\nBounded questions [B03、B04].\n';files['method.md']='# Ordinary method\nSeparate source evidence from interpretation.\n';files['website-handoff.json']={'auto_publish':False,'private_research_export':False,'allowed_media':[],'b_project_id':None,'video_mapping':None,'candidate_markdown':'method.md'};files['公开边界与逐句待核表.json']={'pending_sentences':[{'id':'U01','sentence':'unverified real identity','status':'blocked'}]};files['下一批覆盖队列.json']=[{'priority':1,'source_path':str(media),'range':'600–610; maximum one bounded window','question':'Find work action rather than biography','stop_rule':'stop if no work evidence'}]
    sha=pack(folder,files);return db,local,files,sha


class WorkflowTest(unittest.TestCase):
    def test_adjacent_keeps_separate_clock_and_excludes_flagged_or_private_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,f,sha=workflow_fixture(root)
            try:
                before=protected(db);old=video_timeline(db,identity_id('video-group','synthetic-video-group'));r=import_adjacent(db,root/'adjacent',sha,local);self.assertEqual((r['records'],r['timing_exceptions']),(3,1))
                for table,rows in before.items():self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')][:len(rows)],rows)
                after=video_timeline(db,identity_id('video-group','synthetic-video-group'));self.assertEqual(old['stages'],after['stages']);self.assertEqual(old['coverage'],after['coverage']);self.assertEqual(len(after['adjacent']),1)
                new=video_timeline(db,identity_id('video-group','synthetic-video-group/source/synthetic-adjacent'));self.assertFalse(new['input_complete']);self.assertEqual(new['coverage'][0]['timing_flags'][0]['end'],300.1)
                for row in review_search(db,query='NEW162-')['records']:
                    body=review_detail(db,row['id'])['body'];self.assertNotIn('PRIVATE_',canonical(body));self.assertEqual(body['mapping']['links'],[])
                body=review_detail(db,review_search(db,query='NEW162-02')['records'][0]['id'])['body'];self.assertEqual(len(body['source_evidence']['lines']),2);self.assertEqual(body['video_context']['evidence_windows'],[[128.5,136.5],[522.16,534.38]])
                self.assertEqual(import_adjacent(db,root/'adjacent',sha,local)['status'],'already_imported');self.assertTrue(validate(db)['ok'])
            finally:db.close()

    def test_adjacent_rejects_unexcluded_timing_identity_or_seamless_promotion(self):
        for mode in ['flag','exclude','seamless','gap','person','shared']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);db,local,f,sha=workflow_fixture(root)
                try:
                    if mode=='flag':f['覆盖质量与停止依据.json']['runs'][0]['timing_flags']=[]
                    elif mode=='exclude':f['覆盖质量与停止依据.json']['excluded'].pop()
                    elif mode=='person':f['delta_162_独立增量.json']['records'][0]['person_id']='known-person'
                    else:f['来源身份与相邻依据.json'][{'seamless':'seamless_confirmed','gap':'conditional_gap_seconds','shared':'shared_global_timeline'}[mode]]=True if mode!='gap' else 0
                    sha=pack(root/'adjacent',f);before=protected(db)
                    with self.assertRaises(ValueError):import_adjacent(db,root/'adjacent',sha,local)
                    self.assertEqual(before,protected(db))
                finally:db.close()

    def test_namespaced_workflow_concept_evidence_queue_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,f,sha=workflow_fixture(root)
            try:
                import_adjacent(db,root/'adjacent',sha,local);before=protected(db);result=import_workflow(db,root/'adjacent',sha,local);self.assertEqual(result['registry_entries'],2);self.assertEqual(result['namespace_counts'],{'artist_archive':1,'b_public_library':1});self.assertEqual(before,protected(db))
                listing=workflow_list(db);self.assertEqual(listing['kind_counts']['workflow-registry'],2);concept=next(r for r in listing['records'] if r['kind']=='workflow-concept');body=workflow_detail(db,concept['id'])['body']['record'];self.assertTrue(body['falsifying_evidence_needed']);self.assertTrue(body['alternative_explanation']);self.assertTrue(body['not_event_fact'])
                self.assertEqual(import_workflow(db,root/'adjacent',sha,local)['status'],'already_imported');self.assertEqual(db.execute("SELECT count(*) FROM job WHERE kind='bounded_research'").fetchone()[0],1);self.assertEqual(public_projection(db,'github-index')['count'],0)
                self.assertTrue(validate(db)['ok'])
            finally:db.close()

    def test_immutable_states_stale_guard_and_private_draft_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,f,sha=workflow_fixture(root)
            try:
                import_adjacent(db,root/'adjacent',sha,local);r=import_workflow(db,root/'adjacent',sha,local);d=workflow_detail(db,r['batch_id']);before=d['body'];updated=record_action(db,local,d['id'],'validated',d['state_token'],'Synthetic complete checks');self.assertEqual(updated['state'],'validated');self.assertEqual(updated['body'],before)
                with self.assertRaises(ValueError):record_action(db,local,d['id'],'revision_needed',d['state_token'],'Stale transition')
                private=workflow_detail(db,identity_id('workflow-article','research-162/private-narrative'))
                with self.assertRaises(ValueError):record_action(db,local,private['id'],'published_verified',private['state_token'],'Cannot publish private draft')
                self.assertTrue(validate(db)['ok'])
            finally:db.close()

    def test_method_receipt_requires_exact_revision_approval_and_real_readback_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();db,local,f,sha=workflow_fixture(root)
            try:
                import_adjacent(db,root/'adjacent',sha,local);import_workflow(db,root/'adjacent',sha,local);d=workflow_detail(db,identity_id('workflow-article','research-162/method-candidate'));approval=root/'approval.txt';approval.write_text('Synthetic solewriter approval for this method revision.');readback=root/'readback.html';readback.write_text('<p>Synthetic published method.</p>')
                receipt={'schema':'p4.workflow.publication-receipt/1','document_id':d['id'],'document_digest':d['digest'],'scope':'method_only','publisher':'solewriter','approval_evidence_path':str(approval),'approval_evidence_sha256':file_hash(approval),'url':'https://example.invalid/method','deployment_id':'synthetic-deploy','published_content_sha256':file_hash(readback),'readback_path':str(readback),'readback_at':'2026-01-01T00:00:00+00:00','checks':{'links_verified':True,'method_only':True,'private_material_excluded':True}};p=root/'receipt.json'
                for key,bad in [('document_digest','0'*64),('scope','private_video'),('published_content_sha256','0'*64)]:
                    wrong={**receipt,key:bad};p.write_text(canonical(wrong))
                    with self.assertRaises(ValueError):import_receipt(db,local,d['id'],p,file_hash(p),d['state_token'])
                    self.assertEqual(workflow_detail(db,d['id'])['state'],'editorial_review_required')
                p.write_text(canonical(receipt));updated=import_receipt(db,local,d['id'],p,file_hash(p),d['state_token']);self.assertEqual(updated['state'],'published_verified');self.assertEqual(public_projection(db,'github-index')['count'],0);self.assertTrue(validate(db)['ok'])
            finally:db.close()

    def test_protocol_or_registry_drift_cannot_create_workflow_or_jobs(self):
        for mode in ['protocol','duplicate_registry']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);db,local,f,sha=workflow_fixture(root)
                try:
                    v=f['长期准则保存与读回核验.json']
                    if mode=='protocol':Path(v['fixed_path']).write_text('Changed method not approved for this snapshot')
                    else:
                        data=list(csv.DictReader(io.StringIO(Path(v['registry']).read_text(encoding='utf-8-sig'))));data[1]['registry_key']=data[0]['registry_key'];Path(v['registry']).write_bytes(csv_bytes(data));v['registry_sha256']=file_hash(v['registry']);sha=pack(root/'adjacent',f)
                    import_adjacent(db,root/'adjacent',sha,local);before=[tuple(r) for r in db.execute('SELECT * FROM identity_record')]
                    with self.assertRaises(ValueError):import_workflow(db,root/'adjacent',sha,local)
                    self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM identity_record')]);self.assertEqual(db.execute("SELECT count(*) FROM job WHERE kind='bounded_research'").fetchone()[0],0)
                finally:db.close()

    def test_full_restore_retains_workflow_state_and_original_private_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,f,sha=workflow_fixture(root)
            try:
                import_adjacent(db,root/'adjacent',sha,local);r=import_workflow(db,root/'adjacent',sha,local);d=workflow_detail(db,r['batch_id']);record_action(db,local,d['id'],'validated',d['state_token'],'Synthetic verified import');backup(db,local,root/'backup.zip');restore(root/'backup.zip',root/'restored');other=connect(root/'restored/vault.sqlite3')
                try:self.assertEqual(workflow_list(db),workflow_list(other));self.assertEqual(workflow_detail(db,d['id']),workflow_detail(other,d['id']));self.assertEqual(protected(db),protected(other));self.assertTrue(validate(other)['ok'])
                finally:other.close()
            finally:db.close()
