import copy
import hashlib
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from p4vault.core import *
from p4vault.ingest import import_bundle,import_catalog,import_fixed25
from p4vault.publish import grant,public_projection,revoke,write_public
from p4vault.backup import backup,restore
from p4vault.server import make_server

FIXTURE=json.loads((ROOT/'fixtures/synthetic.json').read_text())
class SystemTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.local=self.root/'local';self.local.mkdir();self.dbpath=self.local/'vault.sqlite3';self.db=connect(self.dbpath)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def load(self,bundle=None,namespace='synthetic'):
        return import_bundle(self.db,copy.deepcopy(bundle or FIXTURE),namespace,'synthetic fixture',self.local)
    def clear(self,id):
        e=get_entity(self.db,id)['record'];e['permission']={'state':'public_approved','hold':False,'basis':'Synthetic test only'}
        with self.db:put_entity(self.db,e,'Synthetic approval')
    def review(self,id,fields=None,destination='github-index'):
        return {'decision':'approved','destination':destination,'approved_by':'synthetic-test','evidence':'fixture-only','records':[{'id':id,'digest':get_entity(self.db,id)['digest'],'fields':fields or ['title','text']}]}
    def test_fresh_schema_and_idempotency(self):
        a=self.load();b=self.load();self.assertEqual(b['status'],'already_imported');self.assertEqual(self.db.execute('select count(*) from revision').fetchone()[0],len(FIXTURE['entities']));self.assertTrue(validate(self.db)['ok']);self.assertEqual(self.db.execute('select max(version) from schema_migration').fetchone()[0],4)
    def test_versions_and_contradiction_survive(self):
        self.load();b=copy.deepcopy(FIXTURE);b['entities'][0]['title']='修订后的项目';self.load(b);self.assertEqual(get_entity(self.db,'demo-project')['version'],2);self.assertEqual(self.db.execute("select count(*) from relation where predicate='contradicts'").fetchone()[0],1)
    def test_invalid_batch_rolls_back(self):
        b=copy.deepcopy(FIXTURE);b['entities'][0]['refs']=['missing']
        with self.assertRaises(ValueError):self.load(b)
        self.assertEqual(self.db.execute('select count(*) from entity').fetchone()[0],0)
    def test_source_zero_time_and_bounds(self):
        b=copy.deepcopy(FIXTURE);next(e for e in b['entities'] if e['kind']=='segment')['end']=999
        with self.assertRaises(ValueError):self.load(b)
        for bad in (-1,float('nan'),float('inf')):
            b=copy.deepcopy(FIXTURE);next(e for e in b['entities'] if e['kind']=='segment')['start']=bad
            with self.assertRaises(ValueError):self.load(b)
    def test_kind_change_rejected(self):
        self.load();e=get_entity(self.db,'demo-project')['record'];e['kind']='claim'
        with self.assertRaises(ValueError):put_entity(self.db,e,'invalid')
    def test_unicode_and_literal_search(self):
        self.load();self.assertTrue(search(self.db,'排练'));self.assertEqual(search(self.db,"%' OR 1=1 --"),[])
    def test_graph_timeline_and_identity_warnings(self):
        self.load();self.assertTrue(graph(self.db,'demo-claim')['edges']);self.assertEqual(timeline(self.db)[0]['event_time']['precision'],'month');self.assertTrue(any(x['code']=='identity_unresolved' for x in validate(self.db)['warnings']))
    def test_content_hash_dedup_does_not_merge(self):
        self.load();e=copy.deepcopy(get_entity(self.db,'demo-asset')['record']);e['id']='demo-copy'
        with self.db:put_entity(self.db,e,'copy')
        self.assertEqual(len(duplicate_candidates(self.db)),1);self.assertNotEqual(get_entity(self.db,'demo-copy')['record']['id'],'demo-asset')
    def test_default_no_publication(self):
        self.load();self.assertEqual(public_projection(self.db,'github-index')['count'],0)
        with self.assertRaises(ValueError):write_public(self.db,'github-index',self.root/'empty.json')
        with self.assertRaises(ValueError):grant(self.db,self.review('demo-project'))
    def test_grant_revision_destination_and_revocation(self):
        self.load();self.clear('demo-project');gid=grant(self.db,self.review('demo-project'))[0]
        self.assertEqual(public_projection(self.db,'github-index')['count'],1);self.assertEqual(public_projection(self.db,'public-site')['count'],0)
        revoke(self.db,gid,'Synthetic withdrawal');self.assertEqual(public_projection(self.db,'github-index')['count'],0)
    def test_stale_grant_blocks(self):
        self.load();self.clear('demo-project');grant(self.db,self.review('demo-project'));e=get_entity(self.db,'demo-project')['record'];e['text']='Changed'
        with self.db:put_entity(self.db,e,'revision')
        with self.assertRaises(ValueError):public_projection(self.db,'github-index')
    def test_private_fields_and_sensitive_payload_block(self):
        self.load();self.clear('demo-project')
        with self.assertRaises(ValueError):grant(self.db,self.review('demo-project',['metadata']))
        e=get_entity(self.db,'demo-project')['record'];e['text']='private@example.test'
        with self.db:put_entity(self.db,e,'test sensitive')
        with self.assertRaises(ValueError):grant(self.db,self.review('demo-project'))
    def test_dangling_public_ref_blocks(self):
        self.load();self.clear('demo-claim');grant(self.db,self.review('demo-claim',['title','refs']))
        with self.assertRaises(ValueError):public_projection(self.db,'github-index')
    def test_inherited_hold_blocks(self):
        self.load();self.clear('demo-event');grant(self.db,self.review('demo-event',['title']))
        e=get_entity(self.db,'demo-asset')['record'];e['permission']['hold']=True
        with self.db:put_entity(self.db,e,'hold')
        with self.assertRaises(ValueError):public_projection(self.db,'github-index')
    def test_jobs_idempotent_and_state_machine(self):
        self.load();jobs=self.db.execute('select * from job').fetchall();self.assertTrue(jobs)
        id=jobs[0]['id']
        with self.db:set_job_state(self.db,id,'running');set_job_state(self.db,id,'done')
        with self.assertRaises(ValueError):set_job_state(self.db,id,'running')
    def test_audit_and_revision_tamper_detected(self):
        self.load();self.db.execute("update audit set detail='{}' where seq=1");self.db.execute("update revision set digest='bad' where entity_id='demo-project'")
        codes={e['code'] for e in validate(self.db)['errors']};self.assertIn('audit_chain',codes);self.assertIn('revision_digest',codes)
    def test_backup_restore_full_history(self):
        self.load();b=copy.deepcopy(FIXTURE);b['entities'][0]['text']='next revision';self.load(b)
        target=self.root/'backup.zip';backup(self.db,self.local,target);restored=self.root/'restored';restore(target,restored)
        d=connect(restored/'vault.sqlite3')
        try:self.assertEqual(get_entity(d,'demo-project')['version'],2);self.assertTrue(validate(d)['ok']);self.assertEqual(d.execute('select count(*) from import_batch').fetchone()[0],2)
        finally:d.close()
        with self.assertRaises(ValueError):restore(target,restored)
    def test_backup_path_traversal_rejected(self):
        target=self.root/'bad.zip'
        with zipfile.ZipFile(target,'w') as z:z.writestr('../outside','x');z.writestr('BACKUP_MANIFEST.json','{}')
        with self.assertRaises(ValueError):restore(target,self.root/'bad-restore')
        self.assertFalse((self.root/'outside').exists())
    def test_backup_tamper_rejected(self):
        self.load();target=self.root/'backup.zip';backup(self.db,self.local,target);bad=self.root/'bad.zip'
        with zipfile.ZipFile(target) as src,zipfile.ZipFile(bad,'w') as dst:
            for name in src.namelist():dst.writestr(name,b'corrupt' if name=='vault.sqlite3' else src.read(name))
        with self.assertRaises(ValueError):restore(bad,self.root/'bad-restore')
    def test_migration_checksum_and_newer_version(self):
        self.db.execute("update schema_migration set sha256='bad' where version=1");self.db.commit()
        with self.assertRaises(ValueError):migrate(self.db)
    def test_http_loopback_read_only_and_host_origin(self):
        self.load();server=make_server(self.dbpath,self.local,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            port=server.server_address[1]
            def request(path='/',headers=None,method='GET'):
                c=http.client.HTTPConnection('127.0.0.1',port);c.request(method,path,headers=headers or {});r=c.getresponse();body=r.read();status=r.status;c.close();return status,body
            self.assertEqual(request()[0],200);self.assertEqual(request('/api/status')[0],200);self.assertEqual(request('/api/graph')[0],200)
            self.assertEqual(request(headers={'Host':'evil.test'})[0],403);self.assertEqual(request(headers={'Origin':'https://evil.test'})[0],403)
            navigation={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
            status,shell=request(headers=navigation);self.assertEqual(status,200);self.assertNotIn(b'demo-person',shell)
            self.assertEqual(request('/api/entity?id=demo-person',headers=navigation)[0],403)
            self.assertEqual(request(headers={**navigation,'Origin':'https://evil.test'})[0],403)
            self.assertEqual(request(headers={**navigation,'Sec-Fetch-Dest':'iframe'})[0],403)
            self.assertEqual(request('/../local/vault.sqlite3')[0],404);self.assertEqual(request('/api/import',method='POST')[0],405)
        finally:server.shutdown();server.server_close();thread.join()
    def test_legacy_adapter_readonly_and_source_zero(self):
        p=self.root/'legacy.sqlite3';src=sqlite3.connect(p)
        src.executescript("""CREATE TABLE assets(id,path,name,bytes,source_sha256,verification,family_hint,publication_hold,hold_evidence);
        CREATE TABLE documents(id,source_id,path,sha256,coverage_kind,window_start,window_end,model);
        CREATE TABLE segments(id,source_id,document_id,start,end,text,quality);
        CREATE TABLE cards(id,title,source_id,start,end,description,context,evidence,publication_hold);""")
        src.execute("INSERT INTO assets VALUES('loc_demo','/synthetic/source.mov','Synthetic media',1,NULL,'metadata','hint',0,'')")
        src.execute("INSERT INTO documents VALUES('d','loc_demo','/synthetic/asr.json','hash','window',100,120,'synthetic')")
        src.execute("INSERT INTO segments VALUES(1,'loc_demo','d',102,105,'合成机器稿','unreviewed')")
        src.execute("INSERT INTO cards VALUES('c','Synthetic card','loc_demo',102,105,'','','',0)")
        src.commit();src.close();before=file_hash(p)
        a=import_catalog(self.db,p,self.local);b=import_catalog(self.db,p,self.local)
        self.assertEqual(a['inserted'],4);self.assertEqual(b['status'],'already_imported');self.assertEqual(file_hash(p),before)
        seg=next(e for e in entities(self.db) if e['kind']=='segment');self.assertEqual(seg['start'],102);self.assertFalse(seg['reliability']['human_reviewed'])
    def test_graph_adapter_snapshot_hash_and_replay(self):
        from p4vault.graph_adapter import import_graph
        folder=self.root/'fixed';(folder/'graph').mkdir(parents=True);(folder/'research/96').mkdir(parents=True)
        raw={'meta':{},'nodes':[{'id':'合成事件','type':'事件','label':'Synthetic event','summary':'synthetic','date':'2020','date_kind':'document'}],'sources':[{'id':'s','path':'/synthetic/note','text':'synthetic'}],'video_index':[{'id':'loc_test','workbench_id':'loc_test','path':'/synthetic/video','name':'Synthetic','duration_seconds':100}], 'edges':[{'id':'e','from_id':'合成事件','to_id':'合成事件','verb':'supports synthetic','source':'s'}], 'video_markers':[{'id':'m','video_id':'loc_test','start_seconds':2,'end_seconds':4,'title':'Synthetic marker','edge_ids':['e'],'event_ids':['合成事件']}], 'role_records':[]}
        (folder/'graph/graph.json').write_text(canonical(raw));(folder/'research/96/事件证据锚点.json').write_text(canonical([{'id':'a','title':'Synthetic candidate','claim':'No real material'}]))
        members=[]
        for name in ['graph/graph.json','research/96/事件证据锚点.json']:
            p=folder/name;members.append({'path':name,'sha256':file_hash(p),'bytes':p.stat().st_size})
        manifest=folder/'MANIFEST.json';manifest.write_text(canonical(members));sha=file_hash(manifest)
        a=import_graph(self.db,folder,sha,self.local);b=import_graph(self.db,folder,sha,self.local)
        self.assertEqual(b['status'],'already_imported');self.assertEqual(a['unmerged_batch96'],1);self.assertEqual(sum(e['kind']=='segment' for e in entities(self.db)),1)
        self.assertFalse(any(e['reliability']['human_reviewed'] for e in entities(self.db)))
        (folder/'graph/graph.json').write_text('{}')
        with self.assertRaises(ValueError):import_graph(self.db,folder,sha,self.local)
    def test_public_claim_requires_projected_sources(self):
        self.load();self.clear('demo-claim');grant(self.db,self.review('demo-claim',['title']))
        with self.assertRaises(ValueError):public_projection(self.db,'github-index')
    def test_relation_tamper_blocks_publication(self):
        self.load();self.db.execute("UPDATE relation SET digest='bad' WHERE id='demo-support'");self.db.commit()
        self.assertFalse(validate(self.db)['ok'])
        with self.assertRaises(ValueError):public_projection(self.db,'github-index')
    def test_revision_head_gap_detection(self):
        self.load();self.db.execute("UPDATE entity SET version=2 WHERE id='demo-project'")
        codes={r['code'] for r in validate(self.db)['errors']};self.assertIn('revision_gap',codes);self.assertIn('revision_head_mismatch',codes)

    def test_search_kind_filter_precedes_limit(self):
        self.load()
        with self.db:
            for i in range(120):put_entity(self.db,{'id':'x'+str(i),'kind':'asset','title':'first '+str(i)},'fixture')
        rows=search(self.db,'',kind='person');self.assertEqual([r['id'] for r in rows],['demo-person'])
    def test_public_projection_never_copies_hidden_private_fields(self):
        self.load();e=get_entity(self.db,'demo-project')['record'];e['metadata']={'local_path':'/synthetic/private/path','raw_private_text':'PRIVATE_SENTINEL_99'}
        with self.db:put_entity(self.db,e,'synthetic hidden field')
        self.clear('demo-project');grant(self.db,self.review('demo-project',['title','text']))
        payload=canonical(public_projection(self.db,'github-index'))
        self.assertNotIn('PRIVATE_SENTINEL_99',payload);self.assertNotIn('/synthetic/private/path',payload)
    def test_readonly_ui_related_diff_and_gate_endpoints(self):
        self.load();e=get_entity(self.db,'demo-project')['record'];e['title']='Revised synthetic title'
        with self.db:put_entity(self.db,e,'synthetic UI revision')
        server=make_server(self.dbpath,self.local,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            def get(path):
                c=http.client.HTTPConnection('127.0.0.1',server.server_address[1]);c.request('GET',path);r=c.getresponse();data=json.loads(r.read());c.close();return r.status,data
            status,rows=get('/api/related?id=demo-person');self.assertEqual(status,200);self.assertTrue(any(r['id']=='demo-casting' for r in rows))
            status,d=get('/api/diff?id=demo-project&from=1&to=2');self.assertEqual(status,200);self.assertTrue(any('Revised synthetic title' in line for line in d['lines']))
            self.assertEqual(get('/api/diff?id=demo-project&from=1&to=99')[0],400)
            status,g=get('/api/release-check?destination=github-index');self.assertEqual(status,200);self.assertFalse(g['allowed']);self.assertFalse(g['output_written'])
            self.assertEqual(get('/api/jobs?state=done')[1],[])
            self.assertEqual([r['kind'] for r in get('/api/search?kind=person')[1]],['person'])
        finally:server.shutdown();server.server_close();thread.join()

    def test_local_alignment_never_extrapolates(self):
        self.load();target={'id':'demo-target','kind':'asset','title':'Synthetic second camera','metadata':{'duration_seconds':120}}
        alignment={'id':'demo-alignment','kind':'alignment','title':'Synthetic local alignment','alignment':{'scope':'local_interval','model':'constant_offset','source_id':'demo-asset','target_source_id':'demo-target','source_start':10,'source_end':20,'offset_seconds':5,'uncertainty_seconds':None,'uncertainty_note':'Synthetic uncertainty remains unknown','basis':'Synthetic local anchor only','time_basis':'source_zero','extrapolation_allowed':False}}
        with self.db:put_entity(self.db,target,'fixture');put_entity(self.db,alignment,'fixture')
        self.assertTrue(validate(self.db)['ok']);self.assertEqual(map_local_time(alignment,15)['target_seconds'],20)
        for time in [0,21,float('nan')]:
            with self.assertRaises(ValueError):map_local_time(alignment,time)
        alignment['alignment']['extrapolation_allowed']=True
        with self.assertRaises(ValueError):normalized(alignment)
    def test_local_alignment_checks_target_duration(self):
        self.load();e={'id':'bounded','kind':'alignment','title':'Synthetic invalid target mapping','alignment':{'scope':'local_interval','model':'constant_offset','source_id':'demo-asset','target_source_id':'demo-asset','source_start':100,'source_end':110,'offset_seconds':20,'uncertainty_seconds':1,'uncertainty_note':'Synthetic uncertainty','basis':'Synthetic','time_basis':'source_zero','extrapolation_allowed':False}}
        with self.db:put_entity(self.db,e,'fixture')
        self.assertIn('alignment_out_of_bounds',{r['code'] for r in validate(self.db)['errors']})

    def test_delta_requires_exact_base_version_and_hash(self):
        from p4vault.graph_adapter import verify_delta_base
        d={'schema':'p4-keyed-graph-delta-v1','required_base_version':79,'required_base_sha256':'a'*64,'result_version':80}
        for version,sha in [(78,'a'*64),(79,'b'*64)]:
            with self.assertRaises(ValueError):verify_delta_base(d,version,sha)
        self.assertFalse(verify_delta_base(d,79,'a'*64)['applied'])
    def test_phone_scan_distinguishes_alphanumeric_asset_hash(self):
        from p4vault.publish import scan_sensitive
        self.assertEqual(scan_sensitive({'url':'https://example.test/assets/wxbackup-f0001-a13800000000.webp'}),[])
        self.assertIn('5',scan_sensitive({'text':'联系电话 13800000000'}))
    def test_local_preview_is_hash_bound_and_cross_site_blocked(self):
        self.load();folder=self.local/'research_snapshots'/('a'*64);folder.mkdir(parents=True);preview=folder/'pixel.png';preview.write_bytes(b'\x89PNG\r\nsynthetic-only')
        e=get_entity(self.db,'demo-segment')['record'];e['metadata']['preview_ref']={'manifest_sha256':'a'*64,'path':'pixel.png','sha256':file_hash(preview),'actual_frame_seconds':12}
        with self.db:put_entity(self.db,e,'synthetic preview reference')
        server=make_server(self.dbpath,self.local,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            def req(headers=None):
                c=http.client.HTTPConnection('127.0.0.1',server.server_address[1]);c.request('GET','/api/preview?id=demo-segment',headers=headers or {});r=c.getresponse();body=r.read();status=r.status;c.close();return status,body
            self.assertEqual(req()[0],200)
            self.assertEqual(req({'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'})[0],403)
            preview.write_bytes(b'tampered');self.assertEqual(req()[0],400)
            e['metadata']['preview_ref']['path']='../../vault.sqlite3'
            with self.db:put_entity(self.db,e,'invalid preview fixture')
            self.assertEqual(req()[0],400)
        finally:server.shutdown();server.server_close();thread.join()

    def test_graph_upgrade_retains_hash_bound_prior_preview(self):
        from p4vault.graph_adapter import import_graph
        graph_fixture={'meta':{'version':1},'nodes':[],'edges':[],'sources':[],'role_records':[],
            'video_index':[{'id':'synthetic-video','name':'Synthetic','path':'/synthetic/demo.hevc','duration_seconds':30}],
            'video_markers':[{'id':'synthetic-marker','title':'Synthetic frame','video_id':'synthetic-video','start_seconds':10,'end_seconds':20,'location_status':'HEVC fallback'}]}
        other=copy.deepcopy(graph_fixture['video_markers'][0]);other['id']='synthetic-marker-two';graph_fixture['video_markers'].append(other)
        def package(version,preview):
            folder=self.root/('package'+str(version));folder.mkdir();g=copy.deepcopy(graph_fixture);g['meta']['version']=version
            payloads={'graph/graph.json':canonical(g).encode()}
            if preview:
                payloads['previews/pixel.png']=b'synthetic-image-bytes'
                payloads['preview_map.json']=canonical([{'marker_id':'synthetic-marker','package_path':'previews/pixel.png','actual_frame_seconds':12}]).encode()
            for name,b in payloads.items():p=folder/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
            entries=[{'path':name,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for name,b in payloads.items()]
            (folder/'MANIFEST.json').write_text(canonical({'files':entries}))
            return folder,file_hash(folder/'MANIFEST.json')
        f,h=package(1,True);import_graph(self.db,f,h,self.local)
        id=stable_id('segment','p4-research-marker','synthetic-marker');ref=get_entity(self.db,id)['record']['metadata']['preview_ref']
        f,h=package(2,False);import_graph(self.db,f,h,self.local)
        self.assertEqual(get_entity(self.db,id)['record']['metadata']['preview_ref'],ref)
        (self.local/'research_snapshots'/ref['manifest_sha256']/ref['path']).write_bytes(b'tampered')
        f,h=package(3,False)
        with self.assertRaises(ValueError):import_graph(self.db,f,h,self.local)

    def test_release_gate_requires_approved_hashed_public_payload(self):
        from tools.release_gate import inspect
        release=self.root/'release';release.mkdir();(release/'public').mkdir()
        (release/'release-files.json').write_text(canonical({'scope':'code-and-synthetic-only','files':[]}))
        payload=release/'public/data.json';payload.write_text(canonical({'text':'Synthetic public text'}))
        manifest={'format':'p4-approved-public-release-v1','approval':'pending','repository':'example/archive','visibility':'private','files':[{'path':'public/data.json','bytes':payload.stat().st_size,'sha256':file_hash(payload)}]}
        (release/'public/manifest.json').write_text(canonical(manifest));self.assertFalse(inspect(release)['ok'])
        manifest['approval']='explicit-user-approved';(release/'public/manifest.json').write_text(canonical(manifest));self.assertTrue(inspect(release)['ok'])
        payload.write_text(canonical({'text':'Contact 13800000000'}));manifest['files'][0].update(bytes=payload.stat().st_size,sha256=file_hash(payload));(release/'public/manifest.json').write_text(canonical(manifest));self.assertFalse(inspect(release)['ok'])

    def test_tracked_privacy_scan_rejects_data_and_credentials(self):
        from tools.check_repository import privacy_codes
        self.assertIn('forbidden_tracked_path_or_binary',privacy_codes('local/vault.sqlite3',b'synthetic'))
        token=('github_'+'pat_'+'A'*40).encode()
        self.assertIn('credential_signature',privacy_codes('fixture.json',token))
        self.assertIn('private_absolute_path',privacy_codes('notes.md',('/'+'Users'+'/synthetic/private.txt').encode()))
    def test_tracked_privacy_scan_bounds_synthetic_exceptions(self):
        from tools.check_repository import privacy_codes
        synthetic=b'private@example.test 13800000000'
        self.assertEqual(privacy_codes('tests/synthetic.py',synthetic),[])
        self.assertIn('contact_phone',privacy_codes('public/source.json',synthetic))
        self.assertIn('contact_email',privacy_codes('README.md',synthetic))
    def test_public_shape_rejects_visibility_upgrade_and_dangling_refs(self):
        from tools.check_repository import check_shapes
        d={'format':'p4-evidence-v1','entities':[{'id':'synthetic','kind':'document','title':'Synthetic','text':'Synthetic','public_url':'https://b.p4theater.cn/sources/synthetic','refs':[]}]}
        source={'records':[{'id':'synthetic','url':d['entities'][0]['public_url'],'response_sha256':'a'*64}]}
        media={'records':[{'video_id':'abcdefghijk','public_url':'https://www.youtube.com/watch?v=abcdefghijk','current_visibility':'unknown','playback_verified':False,'original_media_in_repository':False,'source_document_ids':['synthetic']}],'count':1}
        scope={'included_document_records':1,'media_reference_records':1,'visibility':'private','public_site_republication_granted':False,'raw_archive_backup_claim':False}
        self.assertEqual(check_shapes(d,source,media,scope,{'relations':[]}),[])
        media['records'][0]['current_visibility']='public';d['entities'][0]['refs']=['missing']
        failures=check_shapes(d,source,media,scope,{'relations':[]})
        self.assertIn('media_status_requires_separate_evidence_review',failures);self.assertIn('dangling_document_reference',failures)

    def test_private_raw_snapshot_retained_exactly(self):
        self.load();row=self.db.execute('select * from import_batch').fetchone();self.assertEqual(file_hash(row['snapshot_path']),row['sha256']);self.assertTrue(Path(row['snapshot_path']).is_relative_to(self.local))



class PreviewMapCompatibilityTest(unittest.TestCase):
    def test_frozen_preview_aliases_and_ambiguity(self):
        from p4vault.graph_adapter import normalize_preview_map
        legacy={'marker_id':'marker-one','package_path':'previews/example.jpg','actual_frame_seconds':5.0}
        modern={'marker':'marker-one','package_path':'previews/example.jpg','actual_seconds':5.0}
        for row in (legacy,modern):
            original=dict(row)
            result=normalize_preview_map([row])['marker-one']
            self.assertEqual(result['marker_id'],'marker-one')
            self.assertEqual(result['actual_frame_seconds'],5.0)
            self.assertEqual(row,original)
        for rows in ([legacy,modern],[{**modern,'marker_id':'other'}],[{**modern,'actual_frame_seconds':7}],[{**modern,'actual_seconds':-1}],[{**modern,'actual_seconds':float('nan')}],[{**modern,'actual_seconds':True}]):
            with self.subTest(rows=rows),self.assertRaises(ValueError):normalize_preview_map(rows)



class ReviewedMediaVisibilityTest(unittest.TestCase):
    def fixture(self):
        evidence={'method':'youtube_data_api_official_readback','video_id':'abcdefghijk','official_readback_sha256':'a'*64,'observed_at':'2026-01-01T00:00:00+00:00','privacy_status':'public','upload_status':'processed','processing_status':'succeeded','embeddable':True,'independent_playback_verified':False}
        permission={'state':'explicit_user_approved','destination':'github:synthetic/archive','scope':'public_media_metadata_and_access_link','original_media_upload_to_repository':False}
        record={'video_id':'abcdefghijk','current_visibility':'public','verification':evidence,'permission':permission,'embeddable':True,'source_document_ids':['synthetic']}
        scope={'repository':'synthetic/archive','approved_public_media':[{'video_id':'abcdefghijk','destination':'github:synthetic/archive','permission_basis':'explicit_user_approved','official_readback_sha256':'a'*64}]}
        return record,scope
    def test_explicit_review_and_official_status_allow_one_public_reference(self):
        from tools.check_repository import reviewed_media_visibility
        record,scope=self.fixture();self.assertTrue(reviewed_media_visibility(record,scope))
        self.assertTrue(reviewed_media_visibility({'current_visibility':'unknown'},scope))
    def test_public_watch_link_can_be_nonembeddable_and_region_restricted(self):
        from tools.check_repository import reviewed_media_visibility
        record,scope=self.fixture()
        record['embeddable']=record['verification']['embeddable']=False
        record['region_restriction']={'blocked':['RU']}
        record['verification']['region_restriction']={'blocked':['RU']}
        self.assertTrue(reviewed_media_visibility(record,scope))
        record['embed_url']='https://www.youtube.com/embed/abcdefghijk'
        self.assertFalse(reviewed_media_visibility(record,scope))

    def test_embedding_and_region_restrictions_must_match_official_evidence(self):
        import copy
        from tools.check_repository import reviewed_media_visibility
        original,scope=self.fixture()
        original['embeddable']=original['verification']['embeddable']=False
        original['region_restriction']={'blocked':['RU']}
        original['verification']['region_restriction']={'blocked':['RU']}
        for replacement in [None,{}, {'blocked':[]}, {'allowed':['RU']}]:
            record=copy.deepcopy(original);record['region_restriction']=replacement
            self.assertFalse(reviewed_media_visibility(record,scope))
        for malformed in [{'blocked':'RU'},{'blocked':['RU','RU']},{'blocked':['Russia']},{'blocked':['RU'],'allowed':['US']},{'unrestricted':True}]:
            record=copy.deepcopy(original)
            record['region_restriction']=record['verification']['region_restriction']=malformed
            self.assertFalse(reviewed_media_visibility(record,scope))
        record=copy.deepcopy(original);record['embeddable']=True
        self.assertFalse(reviewed_media_visibility(record,scope))
        for invalid in [None,0,1,'false']:
            record=copy.deepcopy(original);record['embeddable']=record['verification']['embeddable']=invalid
            self.assertFalse(reviewed_media_visibility(record,scope))
        record=copy.deepcopy(original);record['verification']['independent_playback_verified']=True
        self.assertFalse(reviewed_media_visibility(record,scope))

    def test_public_visibility_cannot_be_inferred_or_reused_for_other_media(self):
        from tools.check_repository import reviewed_media_visibility
        import copy
        original,scope=self.fixture()
        mutations=[('video_id','othermedia1'),('verification',{}),('permission',{}),('embeddable',False)]
        for key,value in mutations:
            row=copy.deepcopy(original);row[key]=value
            with self.subTest(key=key):self.assertFalse(reviewed_media_visibility(row,scope))
        for key,value in [('privacy_status','unlisted'),('upload_status','uploaded'),('processing_status','processing'),('observed_at','invalid'),('official_readback_sha256','b'*64),('independent_playback_verified',True)]:
            row=copy.deepcopy(original);row['verification'][key]=value
            with self.subTest(evidence=key):self.assertFalse(reviewed_media_visibility(row,scope))
        self.assertFalse(reviewed_media_visibility(original,{'repository':'synthetic/archive'}))
        changed=copy.deepcopy(scope);changed['repository']='synthetic/elsewhere'
        self.assertFalse(reviewed_media_visibility(original,changed))




class PublicStillMetadataTest(unittest.TestCase):
    def fixture(self):
        from tools.check_repository import still_payload_digest
        record,scope=ReviewedMediaVisibilityTest().fixture();record.update(title='Synthetic film',version='edited_stage_film',platform_duration='PT2M')
        urls={'zh':'https://b.p4theater.cn/viewing/synthetic','en':'https://b.p4theater.cn/en/viewing/synthetic','project':'https://b.p4theater.cn/projects/synthetic','research':'https://b.p4theater.cn/questions/synthetic'}
        frame={'id':'synthetic-60s','url':'/assets/viewing-stills/synthetic-60s.webp','sha256':'b'*64,'width':1280,'height':720,'time_seconds':60,'watch_url':'https://www.youtube.com/watch?v=abcdefghijk&t=60s','source_basis':'exact_uploaded_file_zero_offset','title_zh':'合成静帧','title_en':'Synthetic still','caption_zh':'合成场景','caption_en':'Synthetic scene','alt_zh':'合成描述','alt_en':'Synthetic description','image_url':'https://b.p4theater.cn/assets/viewing-stills/synthetic-60s.webp','page_zh':urls['zh']+'#synthetic-60s','page_en':urls['en']+'#synthetic-60s','project_url':urls['project'],'research_url':urls['research']}
        payload={'schema':'p4_public_film_stills_v1','status':'production_verified','commit':'c'*40,'deployment':'synthetic-deployment','video_id':'abcdefghijk','edition_title_zh':'合成影片','edition_title_en':'Synthetic film','urls':urls,'new_uploads':0,'new_stills':1,'source_basis':'exact_uploaded_file_zero_offset','source_time_precision_note':'Exact edition only; approximate player seeking; no raw mapping','frames':[frame]};record['published_stills']=payload
        scope['approved_public_still_sets']=[{'video_id':'abcdefghijk','destination':'github:synthetic/archive','permission_basis':'explicit_user_approved','payload_digest':still_payload_digest(payload),'scope':'published_still_metadata_and_public_links','image_binaries_in_repository':False}]
        return record,scope,list(urls.values())
    def test_stills_are_revision_bound_metadata_of_existing_edition(self):
        from tools.check_repository import reviewed_public_stills
        record,scope,urls=self.fixture();self.assertTrue(reviewed_public_stills(record,scope,urls));record['published_stills']['frames'][0]['caption_en']='Unapproved revision';self.assertFalse(reviewed_public_stills(record,scope,urls))
        record,scope,urls=self.fixture();self.assertFalse(reviewed_public_stills(record,{**scope,'approved_public_still_sets':[]},urls));self.assertFalse(reviewed_public_stills(record,scope,urls[:-1]))
    def test_stills_reject_wrong_video_time_source_duplicates_and_private_fields(self):
        import copy
        from tools.check_repository import reviewed_public_stills,still_payload_digest
        original,base,urls=self.fixture()
        for case in ['video','time','duplicate','basis','caption','path','private','unknown']:
            record=copy.deepcopy(original);scope=copy.deepcopy(base);payload=record['published_stills'];frame=payload['frames'][0]
            if case=='video':frame['watch_url']='https://www.youtube.com/watch?v=othermedia1&t=60s'
            if case=='time':frame['time_seconds']=120;frame['watch_url']='https://www.youtube.com/watch?v=abcdefghijk&t=120s'
            if case=='duplicate':payload['frames'].append(copy.deepcopy(frame));payload['new_stills']=2
            if case=='basis':frame['source_basis']='raw_recording_estimate'
            if case=='caption':frame['alt_en']=''
            if case=='path':frame['image_url']='https://example.test/private.webp'
            if case=='private':frame['local_source_path']='private-local-source'
            if case=='unknown':record['current_visibility']='unknown'
            scope['approved_public_still_sets'][0]['payload_digest']=still_payload_digest(payload)
            with self.subTest(case=case):self.assertFalse(reviewed_public_stills(record,scope,urls))


def embedded_preview_fixture(folder):
    import base64
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
    graph_data={'meta':{'version':1},'nodes':[{'id':'event-'+str(i),'type':'事件','label':'Synthetic event '+str(i),'summary':'Synthetic fixture only','date':'2020','date_kind':'document'} for i in range(1,9)],'edges':[],'sources':[],'role_records':[],
        'video_index':[{'id':'synthetic-preview-video','name':'Synthetic video','path':'/synthetic/preview-video','duration_seconds':30}],'video_markers':[]}
    for i in range(31):
        graph_data['video_markers'].append({'id':'synthetic-window-'+str(i),'title':'Synthetic window '+str(i),'video_id':'synthetic-preview-video','start_seconds':10,'end_seconds':20,'event_ids':['event-'+str(i%8+1)],'preview_path':str(folder/'previews/pixel.png'),'preview_seconds':12 if i%2==0 else 5,'preview_in_window':i%2==0})
    payloads={'graph/graph.json':canonical(graph_data).encode(),'previews/pixel.png':image}
    for name,raw in payloads.items():
        target=folder/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    (folder/'MANIFEST.json').write_text(canonical({'files':[{'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in payloads.items()]}))
    return file_hash(folder/'MANIFEST.json'),graph_data

class EmbeddedPreviewSnapshotTest(unittest.TestCase):
    def test_embedded_preview_import_is_hash_bound_idempotent_and_keeps_frame_limits(self):
        from p4vault.graph_adapter import import_graph
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'package';sha,raw=embedded_preview_fixture(folder);local=root/'local';db=connect(local/'vault.sqlite3')
            try:
                original=(folder/'graph/graph.json').read_bytes();import_graph(db,folder,sha,local)
                segments=[e for e in entities(db) if e['kind']=='segment'];self.assertEqual(len(segments),31)
                self.assertEqual(sum(e['metadata']['preview_ref']['within_window'] for e in segments),16)
                for e in segments:
                    ref=e['metadata']['preview_ref'];self.assertEqual(file_hash(local/'research_snapshots'/sha/ref['path']),ref['sha256'])
                    self.assertEqual(e['permission']['state'],'local_only');self.assertFalse(e['reliability']['human_reviewed'])
                    self.assertNotIn('HEVC',e['metadata']['browser_playback'])
                before=db.execute('SELECT count(*) FROM revision').fetchone()[0]
                self.assertEqual(import_graph(db,folder,sha,local)['status'],'already_imported')
                self.assertEqual(db.execute('SELECT count(*) FROM revision').fetchone()[0],before)
                self.assertEqual((folder/'graph/graph.json').read_bytes(),original)
            finally:db.close()
    def test_embedded_mapping_does_not_guess_external_files_or_accept_unverified_members(self):
        from p4vault.graph_adapter import snapshot_preview_map
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();graph_data={'video_markers':[{'id':'m','preview_path':str(root.parent/'outside.png'),'preview_seconds':5}]}
            self.assertEqual(snapshot_preview_map({},graph_data,root),{})
            for path,seconds,payloads in [('previews/missing.png',5,{}),('../outside.png',5,{}),('previews/p.png',float('nan'),{'previews/p.png':b'image'})]:
                graph_data['video_markers'][0].update(preview_path=path,preview_seconds=seconds)
                with self.subTest(path=path),self.assertRaises(ValueError):snapshot_preview_map(payloads,graph_data,root)
            graph_data['video_markers'][0].update(preview_path=str(root/'previews/p.png'),preview_seconds=5)
            result=snapshot_preview_map({'previews/p.png':b'image'},graph_data,root)
            self.assertEqual(result['m']['package_path'],'previews/p.png')

if __name__=='__main__':unittest.main()


def role_representation_fixture(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    rows=[{'id':'assignment','person_id':'person','context_id':'event','original_edge_id':'credit','source':'source','role':'Synthetic role'}, {'id':'table-only','person_id':'person','context_id':'project','original_edge_id':'other-credit','source':'source','role':'Synthetic role'}]
    raw={'meta':{'version':1},'nodes':[{'id':'person','type':'人物','label':'Synthetic person'}, {'id':'event','type':'事件','label':'Synthetic event'}, {'id':'project','type':'项目','label':'Synthetic project'}, {'id':'assignment','type':'角色记录','label':'Synthetic role','person_id':'person','context_id':'event','original_edge_id':'credit'}, {'id':'position','type':'角色记录','label':'Synthetic role'}], 'sources':[{'id':'source','text':'Synthetic source'}], 'edges':[{'id':'credit','from_id':'person','to_id':'event','source':'source','verb':'reported role'}, {'id':'other-credit','from_id':'person','to_id':'project','source':'source','verb':'reported contribution'}], 'role_records':rows, 'video_index':[{'id':'video','path':'/synthetic/video','duration_seconds':30}], 'video_markers':[{'id':'marker','title':'Synthetic window','video_id':'video','start_seconds':1,'end_seconds':2,'event_ids':['event'],'role_nodes':['position','person','table-only','unresolved','project']}]}
    data=canonical(raw).encode();(folder/'graph').mkdir(exist_ok=True);(folder/'graph/graph.json').write_bytes(data)
    (folder/'MANIFEST.json').write_text(canonical({'files':[{'path':'graph/graph.json','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}]}))
    return file_hash(folder/'MANIFEST.json'),raw

class RoleRepresentationNavigationTest(unittest.TestCase):
    def test_evidence_bound_links_keep_both_ids_and_make_role_paths_queryable(self):
        from p4vault.graph_adapter import import_graph
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);sha,raw=role_representation_fixture(root/'package');local=root/'local';db=connect(local/'vault.sqlite3')
            try:
                import_graph(db,root/'package',sha,local)
                node=lambda id:stable_id('node','p4-research-node',id)
                role=lambda id:stable_id('role','p4-role-record',id)
                claim=lambda id:stable_id('claim','p4-research-edge',id)
                row=get_entity(db,role('assignment'))['record']
                self.assertEqual(row['metadata']['upstream_record'],raw['role_records'][0])
                self.assertIn(node('assignment'),row['refs']);self.assertIn(claim('credit'),row['refs'])
                self.assertNotEqual(node('assignment'),role('assignment'))
                self.assertEqual(get_entity(db,node('assignment'))['record']['metadata']['role_table_entity_id'],role('assignment'))
                table=get_entity(db,role('table-only'))['record'];self.assertIn(claim('other-credit'),table['refs'])
                self.assertIsNone(table['metadata']['representation_links']['graph_node_id'])
                self.assertEqual(get_entity(db,node('position'))['record']['metadata']['representation_kind'],'role_position_without_assignment_row')
                marker=get_entity(db,stable_id('segment','p4-research-marker','marker'))['record'];resolved=marker['metadata']['explicit_role_references']
                self.assertEqual(set(resolved['resolved_ids']),{node('position'),node('person'),role('table-only')})
                self.assertEqual(resolved['unresolved_upstream_ids'],['unresolved','project']);self.assertFalse(resolved['speaker_identity_confirmed'])
                self.assertEqual(marker['metadata']['upstream_record'],raw['video_markers'][0])
                self.assertTrue({node('assignment'),claim('credit')}<={n['id'] for n in graph(db,role('assignment'))['nodes']})
                self.assertIn(marker['id'],{n['id'] for n in graph(db,node('position'))['nodes']})
                navigation=[edge for edge in graph(db,role('assignment'))['edges'] if edge.get('compatibility_navigation')]
                self.assertEqual(len(navigation),2);self.assertTrue(all(edge['counts_as_new_evidence'] is False for edge in navigation))
                self.assertTrue(validate(db)['ok']);before=db.execute('SELECT count(*) FROM revision').fetchone()[0]
                self.assertEqual(import_graph(db,root/'package',sha,local)['status'],'already_imported')
                self.assertEqual(db.execute('SELECT count(*) FROM revision').fetchone()[0],before)
                self.assertEqual(json.loads((root/'package/graph/graph.json').read_text()),raw)
            finally:db.close()

    def test_conflicting_fields_and_same_labels_cannot_create_identity_mapping(self):
        import copy
        from p4vault.graph_adapter import role_representation_links
        with tempfile.TemporaryDirectory() as temp:
            _,raw=role_representation_fixture(temp)
            original=copy.deepcopy(raw)
            self.assertIsNone(role_representation_links(raw)['table-only']['graph_node_id'])
            for field,value in [('person_id','other-person'),('context_id','project'),('original_edge_id','other-credit'),('type','人物')]:
                changed=copy.deepcopy(raw);changed['nodes'][3][field]=value
                self.assertIsNone(role_representation_links(changed)['assignment']['graph_node_id'])
            for field,value in [('source','unknown-source'),('from_id','project')]:
                changed=copy.deepcopy(raw);changed['edges'][0][field]=value;links=role_representation_links(changed)['assignment']
                self.assertIsNone(links['graph_node_id']);self.assertIsNone(links['original_claim_id'])
                self.assertFalse(links['identity_merge'])
            changed=copy.deepcopy(raw);del changed['nodes'][3]['person_id']
            self.assertIsNone(role_representation_links(changed)['assignment']['graph_node_id'])
            self.assertEqual(raw,original)
