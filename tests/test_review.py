import copy
import hashlib
import io
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from p4vault.core import connect,canonical,file_hash,entities,get_entity,stable_id,validate,ROOT,now,put_entity
from p4vault.graph_adapter import import_graph,current_graph_state
from p4vault.review import import_review,review_search,review_detail,review_facets,excerpt_source
from p4vault.publish import public_projection,grant
from p4vault.backup import backup,restore
from p4vault.server import make_server
from tests.test_system import role_representation_fixture


def write_package(folder,data):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    raw=canonical(data).encode();(folder/'review.json').write_bytes(raw)
    (folder/'MANIFEST.json').write_text(canonical({'review.json':hashlib.sha256(raw).hexdigest()}))
    return file_hash(folder/'MANIFEST.json')


def review_fixture(root):
    root=Path(root);source=root/'source.md';source.write_text('Synthetic heading\nSynthetic person recalls an invitation.\nPlanning is not completion.\n')
    _,base=role_representation_fixture(root/'base');base['sources'][0]['path']=str(source)
    raw=canonical(base).encode();(root/'base/graph/graph.json').write_bytes(raw);(root/'base/MANIFEST.json').write_text(canonical({'files':[{'path':'graph/graph.json','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}]}))
    local=root/'local';db=connect(local/'vault.sqlite3');import_graph(db,root/'base',file_hash(root/'base/MANIFEST.json'),local)
    common={'origin':'synthetic','people':['Synthetic person'],'claim':'Synthetic review, not a historical fact','event_date':None,'event_time_note':'Actual date unknown','interview_date':None,'publication_date':'2022-01-02','source':str(source),'anchor':'L2','limit':'Source-bound labels; no identity inference'}
    row={**common,'id':'review-a','project':'Project A','event':'Review stage A','relation':'reported invitation','strength':'one-sided retrospective','original_record':{'from_person':'person','to_person':'unresolved-same-name','base_edge_ids':['credit'],'direct_dialogue_record':False,'direction':'directed_action'}}
    other={**common,'id':'review-b','project':'Project B','event':'Review stage B','relation':'planned cooperation','strength':'advance plan','anchor':'L3','original_record':None}
    data={'schema':'p4.private.people-review/1','visibility':'local_private','review_only':True,'base_graph_sha256':current_graph_state(db)['graph_sha256'],'predecessors':[],'records':[row,other],'sources':{'synthetic':{'path':str(source),'sha256':file_hash(source)}},'inherited_packages':{},'identity_policy':'No name-only merge','event_model':'Review stages are not graph events'}
    sha=write_package(root/'review',data);return db,local,data,sha


class PrivateReviewTest(unittest.TestCase):
    def test_isolated_source_bound_layer_filters_and_no_public_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,data,sha=review_fixture(root)
            try:
                before=entities(db);state=current_graph_state(db);result=import_review(db,root/'review',sha,local,'review.json')
                self.assertEqual(result['records'],2);self.assertEqual(entities(db),before);self.assertEqual(current_graph_state(db),state)
                rows=review_search(db,person='Synthetic person',project='Project A',relation='reported invitation',nature='one-sided retrospective')['records'];self.assertEqual(len(rows),1)
                self.assertEqual(review_search(db,person='Synthetic person',project='Project B',nature='one-sided retrospective')['count'],0)
                person=stable_id('node','p4-research-node','person');self.assertEqual(review_search(db,entity=person)['count'],1)
                detail=review_detail(db,rows[0]['id']);body=detail['body'];self.assertFalse(body['event_is_base_graph_fact']);self.assertIsNone(body['record']['event_date']);self.assertFalse(body['direct_dialogue_record'])
                self.assertEqual(body['source_evidence']['lines'][0]['location'],'L2');self.assertEqual(body['mapping']['explicit_people'][0]['entity_id'],person)
                self.assertTrue(body['mapping']['unresolved']);self.assertFalse(body['mapping']['name_only_merge'])
                other=review_detail(db,review_search(db,project='Project B')['records'][0]['id']);self.assertFalse(other['body']['mapping']['explicit_people'])
                self.assertEqual(public_projection(db,'github-index')['count'],0);self.assertEqual(public_projection(db,'public-site')['count'],0)
                with self.assertRaises(ValueError):grant(db,{'decision':'approved','destination':'github-index','approved_by':'synthetic','evidence':'synthetic','records':[{'id':rows[0]['id'],'digest':'x','fields':['title']}]})
                count=db.execute('SELECT count(*) FROM review_revision').fetchone()[0]
                self.assertEqual(import_review(db,root/'review',sha,local,'review.json')['status'],'already_imported');self.assertEqual(db.execute('SELECT count(*) FROM review_revision').fetchone()[0],count)
                self.assertTrue(validate(db)['ok'])
            finally:db.close()

    def test_explicit_person_and_verified_source_map_without_name_inference(self):
        from p4vault.review import map_record,source_registry
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,data,sha=review_fixture(root)
            try:
                row=copy.deepcopy(data['records'][1]);row['original_record']={'person':'person','source':row['source']}
                mapping=map_record(row,entities(db),source_registry(data))
                self.assertEqual(len(mapping['explicit_people']),1)
                row['original_record']['source']='not-the-declared-source'
                self.assertFalse(map_record(row,entities(db),source_registry(data))['explicit_people'])
                row['original_record']={'person':'unresolved-same-name','source':row['source']}
                self.assertFalse(map_record(row,entities(db),source_registry(data))['explicit_people'])
            finally:db.close()

    def test_invalid_baseline_hash_source_and_anchor_do_not_write_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,data,sha=review_fixture(root)
            try:
                for change in ['base','source','anchor','visibility']:
                    bad=copy.deepcopy(data)
                    if change=='base':bad['base_graph_sha256']='0'*64
                    elif change=='source':bad['sources']['synthetic']['sha256']='0'*64
                    elif change=='anchor':bad['records'][0]['anchor']='L999'
                    else:bad['review_only']=False
                    folder=root/change;checksum=write_package(folder,bad)
                    with self.subTest(change=change),self.assertRaises(ValueError):import_review(db,folder,checksum,local,'review.json')
                    self.assertEqual(db.execute('SELECT count(*) FROM review_record').fetchone()[0],0)
                with self.assertRaises(ValueError):import_review(db,root/'review','0'*64,local,'review.json')
                (root/'review/MANIFEST.json').write_text(canonical({'../source.md':file_hash(root/'source.md')}))
                with self.assertRaises(ValueError):import_review(db,root/'review',file_hash(root/'review/MANIFEST.json'),local,'review.json')
            finally:db.close()

    def test_review_history_backup_restore_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,data,sha=review_fixture(root)
            try:
                import_review(db,root/'review',sha,local,'review.json');changed=copy.deepcopy(data);changed['records'][0]['claim']='A revised synthetic interpretation'
                checksum=write_package(root/'next',changed);import_review(db,root/'next',checksum,local,'review.json')
                id=stable_id('review','p4-private-people-review','review-a');self.assertEqual(review_detail(db,id)['version'],2)
                saved=backup(db,local,root/'backup.zip');restored=restore(saved['path'],root/'restore');rdb=connect(Path(restored['path'])/'vault.sqlite3')
                try:self.assertEqual(review_detail(rdb,id),review_detail(db,id));self.assertTrue(validate(rdb)['ok'])
                finally:rdb.close()
                db.execute('DELETE FROM review_entity_link WHERE review_id=?',(id,));db.commit();self.assertIn('private_review_link_digest',{e['code'] for e in validate(db)['errors']})
                with self.assertRaises(ValueError):public_projection(db,'github-index')
            finally:db.close()

    def test_migration_preserves_old_core_data_and_http_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old.sqlite3';db=sqlite3.connect(old);db.row_factory=sqlite3.Row
            db.execute('CREATE TABLE schema_migration(version INTEGER PRIMARY KEY,sha256 TEXT,applied_at TEXT)')
            for version in (1,2):
                path=next((ROOT/'migrations').glob(f'{version:03}_*.sql'));db.executescript(path.read_text());db.execute('INSERT INTO schema_migration VALUES(?,?,?)',(version,file_hash(path),now()))
            put_entity(db,{'id':'existing','kind':'document','title':'Synthetic prior entity'},'seed');db.commit();before=get_entity(db,'existing');db.close();db=connect(old)
            self.assertEqual(get_entity(db,'existing'),before);self.assertEqual(db.execute('SELECT max(version) FROM schema_migration').fetchone()[0],4);db.close()
            db,local,data,sha=review_fixture(root);import_review(db,root/'review',sha,local,'review.json');db.close();server=make_server(local/'vault.sqlite3',local,0);t=threading.Thread(target=server.serve_forever,daemon=True);t.start();base='http://127.0.0.1:'+str(server.server_address[1])
            try:
                response=json.load(urllib.request.urlopen(base+'/api/reviews?project=Project%20A'));self.assertEqual(response['count'],1)
                self.assertEqual(len(json.load(urllib.request.urlopen(base+'/api/review-facets'))['project']),2)
                detail=json.load(urllib.request.urlopen(base+'/api/review?id='+response['records'][0]['id']));self.assertTrue(detail['body']['review_only'])
                for endpoint in ['/api/reviews','/api/review-facets','/api/review?id='+response['records'][0]['id']]:
                    request=urllib.request.Request(base+endpoint,headers={'Origin':'https://example.test'})
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
                    self.assertEqual(error.exception.code,403);error.exception.close()
            finally:server.shutdown();server.server_close();t.join()

    def test_docx_paragraph_anchor_is_explicit_and_bounded(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/><w:p><w:r><w:t>Heading</w:t></w:r></w:p><w:p><w:r><w:t>Original synthetic passage</w:t></w:r></w:p></w:body></w:document>')
        result=excerpt_source('synthetic.docx',stream.getvalue(),'P2');self.assertEqual(result['lines'],[{'location':'P2','text':'Original synthetic passage'}]);self.assertIn('nonempty',result['numbering'])
        with self.assertRaises(ValueError):excerpt_source('synthetic.docx',stream.getvalue(),'P99')

    def test_append_delta_history_replay_and_successive_overlays(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,base,sha=review_fixture(root)
            try:
                import_review(db,root/'review',sha,local,'review.json');before=entities(db)
                data=copy.deepcopy(base);data['schema']='p4.private.people-review/2';new=copy.deepcopy(base['records'][1]);new['id']='new-c';data['records'].append(new)
                overlay={'id':'review-a','operation':'append_evidence_overlay','supplement_ids':['new-c'],'note':'Synthetic context, not a new discovery','changed_claim':False};data['evidence_overlays']=[overlay]
                delta={'schema':'p4.private.people-delta/2','parent_path':str(root/'review/review.json'),'parent_sha256':file_hash(root/'review/review.json'),'parent_manifest_sha256':sha,'inherited_ids':['review-a','review-b'],'added_records':[new],'evidence_overlays':[overlay],'removed_ids':[],'replaced_records':[],'counts':{'inherited':2,'added':1,'overlays':1,'total':3}}
                checksum=write_delta(root/'next',data,delta)
                result=import_review(db,root/'next',checksum,local,'review.json','delta.json');self.assertEqual(result['revisions_written'],2)
                self.assertEqual(entities(db),before);self.assertEqual(db.execute('SELECT count(*) FROM review_revision').fetchone()[0],4)
                self.assertEqual(import_review(db,root/'next',checksum,local,'review.json','delta.json')['status'],'already_imported')
                a=stable_id('review','p4-private-people-review','review-a');self.assertEqual(review_detail(db,a)['body']['record'],base['records'][0])
                nextdata=copy.deepcopy(data);nextdata['schema']='p4.private.people-review/3';correction={'id':'new-c','operation':'append_provenance_correction','supplement_ids':[],'note':'Previously known; newly structured only'};nextdata['evidence_overlays'].append(correction)
                nextdelta={**delta,'schema':'p4.private.people-delta/3','parent_path':str(root/'next/review.json'),'parent_sha256':file_hash(root/'next/review.json'),'parent_manifest_sha256':checksum,'inherited_ids':['review-a','review-b','new-c'],'added_records':[],'evidence_overlays':[correction],'counts':{'inherited':3,'added':0,'overlays':1,'total':3}}
                nextsha=write_delta(root/'last',nextdata,nextdelta);self.assertEqual(import_review(db,root/'last',nextsha,local,'review.json','delta.json')['revisions_written'],1)
                with self.assertRaises(ValueError):import_review(db,root/'next',checksum,local,'review.json','delta.json')
                self.assertTrue(validate(db)['ok']);self.assertEqual(public_projection(db,'github-index')['count'],0)
            finally:db.close()

    def test_delta_rejects_missing_parent_modified_inheritance_and_overlay_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,base,sha=review_fixture(root)
            try:
                data=copy.deepcopy(base);data['schema']='p4.private.people-review/2';data['evidence_overlays']=[]
                delta={'schema':'p4.private.people-delta/2','parent_path':str(root/'review/review.json'),'parent_sha256':file_hash(root/'review/review.json'),'parent_manifest_sha256':sha,'inherited_ids':['review-a','review-b'],'added_records':[],'evidence_overlays':[],'removed_ids':[],'replaced_records':[],'counts':{'inherited':2,'added':0,'overlays':0,'total':2}}
                checksum=write_delta(root/'next',data,delta)
                with self.assertRaises(ValueError):import_review(db,root/'next',checksum,local,'review.json','delta.json')
                import_review(db,root/'review',sha,local,'review.json')
                for case in ['record','parent_hash','overlay','remove','counts']:
                    bad=copy.deepcopy(data);d=copy.deepcopy(delta)
                    if case=='record':bad['records'][0]['claim']='Unapproved rewrite'
                    if case=='parent_hash':d['parent_sha256']='0'*64
                    if case=='overlay':bad['evidence_overlays']=[{'id':'missing'}]
                    if case=='remove':d['removed_ids']=['review-a']
                    if case=='counts':d['counts']['total']=9
                    checksum=write_delta(root/case,bad,d)
                    with self.subTest(case=case),self.assertRaises(ValueError):import_review(db,root/case,checksum,local,'review.json','delta.json')
                    self.assertEqual(db.execute('SELECT count(*) FROM review_revision').fetchone()[0],2)
            finally:db.close()

    def test_image_evidence_keeps_observation_separate_from_new_review(self):
        from p4vault.review import structured_evidence
        import struct
        raw=b'\x89PNG\r\n\x1a\n'+struct.pack('>I',13)+b'IHDR'+struct.pack('>II',800,2000)
        entry={'path':'synthetic.png','sha256':hashlib.sha256(raw).hexdigest(),'dimensions':[800,2000],'observation':'Synthetic prior visual observation','article':'synthetic'}
        e=structured_evidence('synthetic.png',raw,'Lower half',{'synthetic.png':entry},{})
        self.assertFalse(e['current_import_visual_review']);self.assertEqual(e['lines'],[]);self.assertEqual(e['kind'],'image_document')
        with self.assertRaises(ValueError):structured_evidence('synthetic.png',raw,'Lower half',{'synthetic.png':{**entry,'dimensions':[1,2]}},{})
        gif=b'GIF89a'+struct.pack('<HH',12,18);g={**entry,'path':'synthetic.gif','dimensions':[12,18],'viewed_frames_zero_based':[0,2]};self.assertEqual(structured_evidence('synthetic.gif',gif,'Frames',{'synthetic.gif':g},{})['dimensions'],[12,18])

    def test_html_table_structure_is_verified_without_execution(self):
        from p4vault.review import structured_evidence
        raw=b'<script>never execute()</script><table><tr><td>Author</td><td>Director</td></tr><tr><td>A Person</td><td>B Person</td></tr></table>'
        table={'sha256':hashlib.sha256(raw).hexdigest(),'table_rows':[{'cells':[{'text':'Author'},{'text':'Director'}]},{'cells':[{'text':'A Person'},{'text':'B Person'}]}]}
        e=structured_evidence('source.html',raw,'First two rows',{}, {'source.html':table});self.assertEqual(e['kind'],'html_table');self.assertEqual(e['lines'][2],{'location':'T1R2C1','text':'A Person'})
        changed=copy.deepcopy(table);changed['table_rows'][0]['cells'][0]['text']='Different role'
        with self.assertRaises(ValueError):structured_evidence('source.html',raw,'Rows',{}, {'source.html':changed})


    def test_top_level_source_flags_stage_comparison_and_zero_overlay_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,base,sha=review_fixture(root)
            try:
                import_review(db,root/'review',sha,local,'review.json');data=copy.deepcopy(base);data['schema']='p4.private.people-review/2';new=copy.deepcopy(base['records'][1]);new.update(id='new-c',from_person='A label',to_person='B label',direction='directed_invitation',direct_dialogue_record=False,both_sides_independently_confirmed=False);data['records'].append(new)
                data['project_stages']=[{'id':'stage-group','title':'Synthetic stage','records':['new-c'],'boundary':'Retrospective date only'}];data['research_comparisons']=[{'records':['new-c','review-a'],'type':'different perspectives','claim':'Not a contradiction','causal':False}]
                delta={'schema':'p4.private.people-delta/2','parent_path':str(root/'review/review.json'),'parent_sha256':file_hash(root/'review/review.json'),'parent_manifest_sha256':sha,'inherited_ids':['review-a','review-b'],'added_records':[new],'evidence_overlays':[],'removed_ids':[],'replaced_records':[],'counts':{'inherited':2,'added':1,'total':3}}
                checksum=write_delta(root/'next',data,delta);result=import_review(db,root/'next',checksum,local,'review.json','delta.json');self.assertEqual(result['revisions_written'],1)
                body=review_detail(db,stable_id('review','p4-private-people-review','new-c'))['body'];self.assertFalse(body['direct_dialogue_record']);self.assertFalse(body['both_sides_independently_confirmed']);self.assertEqual(body['direction'],'directed_invitation');self.assertEqual(body['event_title'],new['event']);self.assertEqual(body['event_stage']['title'],'Synthetic stage');self.assertFalse(body['research_comparisons'][0]['causal']);self.assertEqual(body['source_direction_labels']['from_person'],'A label');self.assertFalse(body['mapping']['explicit_people'])
                self.assertEqual(import_review(db,root/'next',checksum,local,'review.json','delta.json')['status'],'already_imported')
            finally:db.close()


    def test_count_aliases_preserve_research_metrics_and_reject_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,base,sha=review_fixture(root)
            try:
                import_review(db,root/'review',sha,local,'review.json');data=copy.deepcopy(base);data['schema']='p4.private.people-review/2'
                counts={'inherited_records':2,'added_records':0,'total':2,'documents_read_or_screened':7,'new_audio':0}
                delta={'schema':'p4.private.people-delta/2','parent_path':str(root/'review/review.json'),'parent_sha256':file_hash(root/'review/review.json'),'parent_manifest_sha256':sha,'inherited_ids':['review-a','review-b'],'added_records':[],'evidence_overlays':[],'removed_ids':[],'replaced_records':[],'counts':counts}
                conflict=copy.deepcopy(delta);conflict['counts']['inherited']=99;checksum=write_delta(root/'bad',data,conflict)
                with self.assertRaises(ValueError):import_review(db,root/'bad',checksum,local,'review.json','delta.json')
                checksum=write_delta(root/'next',data,delta);result=import_review(db,root/'next',checksum,local,'review.json','delta.json');self.assertEqual(result['revisions_written'],0)
                package=json.loads(db.execute('SELECT body FROM review_package WHERE id=?',(result['package_id'],)).fetchone()[0]);self.assertEqual(package['declared_counts'],counts)
                self.assertEqual(import_review(db,root/'next',checksum,local,'review.json','delta.json')['status'],'already_imported');self.assertTrue(validate(db)['ok'])
            finally:db.close()


def write_delta(folder,data,delta):
    folder=Path(folder);write_package(folder,data);(folder/'delta.json').write_text(canonical(delta));manifest={name:file_hash(folder/name) for name in ['review.json','delta.json']};(folder/'MANIFEST.json').write_text(canonical(manifest));return file_hash(folder/'MANIFEST.json')
