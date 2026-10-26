"""Synthetic only. No private names, paths, quotes or source packages in Git."""
import copy
import hashlib
import io
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from p4vault.core import canonical,connect,file_hash,validate,entities,search
from p4vault.graph_adapter import import_graph,current_graph_state
from p4vault.review import import_review
from p4vault.identity import MEMBERS,import_identity,import_confirmation,identity_search,identity_detail,identity_id,direct_paragraphs
from p4vault.backup import backup,restore
from p4vault.publish import public_projection,grant
from p4vault.server import make_server
from tests.test_system import role_representation_fixture
from tests.test_review import write_package


def package(folder,files):
    folder.mkdir(parents=True,exist_ok=True)
    for name,value in files.items():(folder/name).write_text(canonical(value))
    (folder/'MANIFEST.json').write_text(canonical({name:file_hash(folder/name) for name in files}))
    return file_hash(folder/'MANIFEST.json')


def fixture(root):
    source=root/'source.md';source.write_text('Synthetic source\nkey=Person A variant\nA planned participation, not a verified performance.\n')
    _,base=role_representation_fixture(root/'base')
    base['nodes'] += [{'id':'person-b','type':'人物','label':'Person B'},{'id':'person-c','type':'人物','label':'Person C'}]
    base['sources'][0]['path']=str(source)
    for row in base['role_records']:row.update(context='Project A',status='planned',date=None,date_kind='unknown',anchor='2',excerpt='Inherited excerpt',uncertainty='Not independently verified')
    raw=canonical(base).encode();(root/'base/graph/graph.json').write_bytes(raw)
    (root/'base/MANIFEST.json').write_text(canonical({'files':[{'path':'graph/graph.json','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}]}))
    local=root/'local';db=connect(local/'vault.sqlite3');import_graph(db,root/'base',file_hash(root/'base/MANIFEST.json'),local)
    row={'id':'review-a','origin':'synthetic','people':['Person A variant'],'claim':'Synthetic plan','event_date':None,'event_time_note':'Unknown','interview_date':None,'publication_date':None,'source':str(source),'anchor':'L2','limit':'Not performance proof','project':'Project A','event':'Planning','relation':'planned participation','strength':'plan','original_record':None}
    review={'schema':'p4.private.people-review/1','visibility':'local_private','review_only':True,'base_graph_sha256':current_graph_state(db)['graph_sha256'],'predecessors':[],'records':[row],'sources':{'s':{'path':str(source),'sha256':file_hash(source)}},'inherited_packages':{},'identity_policy':'source bounded','event_model':'review only'}
    review_sha=write_package(root/'review',review);import_review(db,root/'review',review_sha,local,'review.json')
    table={k:[] for k in MEMBERS}
    for i,(id,name) in enumerate([('person','Synthetic person'),('person-b','Person B'),('person-c','Person C')]):
        table['card'].append({'person_id':id,'name':name,'identity_kind':'source name only','priority_no_direct_event':i>0,'evidence_ids':['e1','e2'] if i==0 else [],'source_ids':['s'],'explicit_alias_evidence':['a'] if i==0 else [],'qualified_existing_review_links':['b'] if i==0 else [],'new_source_participation_positions':[],'pending_exact_name_review':[],'limits':'No legal identity or voice identification'})
        table['question'].append({'id':'q'+str(i),'question':'Same source names?','paths':[str(source)]})
    for i,r in enumerate(base['role_records'],1):
        table['evidence'].append({'evidence_id':'e'+str(i),'old_role_id':r['id'],'person_id':r['person_id'],'name':'Synthetic person','context_id':r['context_id'],'context':r['context'],'role':r['role'],'old_status':r['status'],'date':r['date'],'date_kind':r['date_kind'],'source_id':r['source'],'source_path':str(source),'anchor':r['anchor'],'old_excerpt':r['excerpt'],'uncertainty':r['uncertainty']})
    table['source']=[{'source_id':'source','path':str(source)},{'source_id':'s','path':str(source)}]
    table['alias']=[{'alias_id':'a','alias':'key','document_name':'Person A variant','person_card_candidate':None,'scope':str(source),'anchor':'L2','excerpt':'key=Person A variant','rule':'Only this document'}]
    table['bridge']=[{'bridge_id':'b','person_id':'person','name':'Synthetic person','existing_review_ids':['review-a'],'references':[{'path':str(source),'anchor':'L2'}],'changes_existing_records':False,'biometric_use':False}]
    table['unmapped']=[{'anonymous_id':'u','label':'Person A variant','source':str(source),'event':'Planning','review_records':['review-a'],'is_new_person':False}]
    delta={'schema':'p4.private.identity-participation/156','base89':str(root/'base/graph/graph.json'),'base89_sha256':file_hash(root/'base/graph/graph.json'),'review151':str(root/'review/review.json'),'review151_sha256':file_hash(root/'review/review.json'),'preserved_person_ids':[x['person_id'] for x in table['card']],'preserved_review_ids':['review-a'],'identity_merges':[],'new_persons':[],'new_whole_events':[],'visibility':'local_private','added_scoped_aliases':table['alias'],'review_link_overlays':table['bridge'],'new_document_positions':[]}
    files={MEMBERS[k][0]:v for k,v in table.items()};files['delta_156_身份参与侧表.json']=delta;files['实际定向读取账.json']=[{'path':str(source),'sha256':file_hash(source)}]
    sha=package(root/'identity',files)
    return db,local,files,sha,review_sha


def confirmation(root,files,sha,review_sha):
    delta=files['delta_156_身份参与侧表.json'];groups=[]
    for i,(id,name,variant) in enumerate([('person','Synthetic person','Person A variant'),('person-b','Person B','B variant'),('person-c','Person C','C variant')]):
        groups.append({'confirmation_id':'c'+str(i),'question_id':'q'+str(i),'canonical_person_id':id,'canonical_name':name,'confirmed_variants':[name,variant],'user_evidence':'user','original_spelling_preserved':True,'scope':'current archive'})
    path=str(root/'source.md');evidence={'source_id':'user','messages':[{'message_id':'synthetic-message','text':'Each group separately confirmed'}],'questions_source':str(root/'identity'/MEMBERS['question'][0]),'questions_sha256':file_hash(root/'identity'/MEMBERS['question'][0])}
    d={'schema':'p4.private.identity-confirmation/157','visibility':'local_private','biometric_identification':False,'new_people':[],'new_interactions':[],'record_replacements':[],'idempotency_key':'synthetic-confirmation','preconditions':{'base89_sha256':delta['base89_sha256'],'156_manifest_sha256':sha,'151_manifest_sha256':review_sha},'confirmation_groups':groups,'evidence':evidence,'alias_resolutions':[{'resolution_id':'ra','prior_alias_id':'a','original_alias':'key','original_document_name':'Person A variant','canonical_person_id':'person','canonical_name':'Synthetic person','confirmation_id':'c0','source_path':path,'anchor':'L2','excerpt':'key=Person A variant'}],'existing_review_person_links':[{'link_id':'rl','existing_review_id':'review-a','old_label':'Person A variant','canonical_person_id':'person','canonical_name':'Synthetic person','source':path,'anchor':'L2','original_claim':'Synthetic plan','original_limit':'Not performance proof','confirmation_id':'c0'}],'position_redirects':[{'prior_anonymous_position_id':'u','original_label':'Person A variant','canonical_person_id':'person','canonical_name':'Synthetic person','source':path,'event':'Planning','review_records':['review-a'],'confirmation_id':'c0','keep_original_id_as_redirect':True}]}
    f={'delta_157_研究确认补证.json':d,'用户确认来源.json':evidence,'三组各自同人_稳定ID映射.json':groups,'十二条原稿标签补通.json':d['alias_resolutions'],'四条既有事件记录人名补通.json':d['existing_review_person_links'],'旧待映射位置可逆指向.json':d['position_redirects']}
    return f,package(root/'confirmation',f)


def protected(db):
    return {name:[tuple(r) for r in db.execute('SELECT * FROM '+name+' ORDER BY rowid')] for name in ['entity','revision','relation','relation_revision','alias','grant_record','review_package','review_record','review_revision','review_entity_link']}


class IdentityTest(unittest.TestCase):
    def test_three_separate_groups_idempotency_search_history_and_export_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db,local,files,sha,rsha=fixture(root)
            try:
                before=protected(db);import_identity(db,root/'identity',sha,local)
                self.assertEqual(identity_search(db)['count'],3);self.assertEqual(identity_search(db,priority=True)['count'],2)
                self.assertEqual(identity_search(db,q='Person A variant')['count'],0)
                f,csha=confirmation(root,files,sha,rsha);import_confirmation(db,root/'confirmation',csha,local)
                for name,target in [('Person A variant','person'),('B variant','person-b'),('C variant','person-c')]:self.assertEqual([r['upstream_id'] for r in identity_search(db,q=name)['records']],[target])
                self.assertEqual(identity_search(db,q='key')['count'],1)
                self.assertEqual([r['title'] for r in search(db,'B variant',kind='person')],['Person B'])
                self.assertEqual(search(db,'key',kind='person'),[])
                self.assertEqual(identity_search(db,context='Project A')['count'],1)
                self.assertEqual(import_identity(db,root/'identity',sha,local)['status'],'already_imported')
                self.assertEqual(import_confirmation(db,root/'confirmation',csha,local)['status'],'already_imported')
                self.assertEqual(before,protected(db));self.assertTrue(validate(db)['ok'])
                self.assertEqual(identity_detail(db,identity_id('unmapped','u'))['related'][0]['kind'],'redirect')
                self.assertEqual(identity_detail(db,identity_id('question','q0'))['body']['state'],'unconfirmed')
                self.assertEqual(len(identity_detail(db,identity_id('question','q0'))['related']),1)
                self.assertEqual(public_projection(db,'github-index')['count'],0)
                with self.assertRaises(ValueError):grant(db,{'decision':'approved','destination':'github-index','approved_by':'synthetic','evidence':'synthetic','records':[{'id':identity_id('card','person'),'digest':'x','fields':['title']}]})
            finally:db.close()

    def test_failed_import_has_no_rows_and_rejects_role_or_anchor_drift(self):
        for mode in ['role','anchor','base','link']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);db,local,f,sha,_=fixture(root)
                try:
                    if mode=='role':f[MEMBERS['evidence'][0]][0]['old_excerpt']='Changed'
                    elif mode=='anchor':f[MEMBERS['alias'][0]][0]['excerpt']='Not in source'
                    elif mode=='base':f['delta_156_身份参与侧表.json']['base89_sha256']='0'*64
                    else:f[MEMBERS['card'][0]][0]['source_ids']=['missing']
                    sha=package(root/'identity',f)
                    with self.assertRaises(ValueError):import_identity(db,root/'identity',sha,local)
                    self.assertEqual(db.execute('SELECT count(*) FROM identity_record').fetchone()[0],0)
                finally:db.close()

    def test_confirmation_rejects_overlapping_groups_and_rewritten_review(self):
        for mode in ['overlap','claim','source']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);db,local,f,sha,rsha=fixture(root)
                try:
                    import_identity(db,root/'identity',sha,local);c,csha=confirmation(root,f,sha,rsha)
                    if mode=='overlap':c['三组各自同人_稳定ID映射.json'][1]['confirmed_variants'].append('Person A variant')
                    elif mode=='claim':c['四条既有事件记录人名补通.json'][0]['original_claim']='Upgraded fact'
                    else:c['十二条原稿标签补通.json'][0]['source_path']='/different-source'
                    csha=package(root/'confirmation',c)
                    with self.assertRaises(ValueError):import_confirmation(db,root/'confirmation',csha,local)
                    self.assertEqual(db.execute("SELECT count(*) FROM identity_record WHERE kind='confirmation'").fetchone()[0],0)
                finally:db.close()

    def test_backup_restore_and_tamper_blocks_public_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db,local,f,sha,rsha=fixture(root)
            try:
                import_identity(db,root/'identity',sha,local);c,csha=confirmation(root,f,sha,rsha);import_confirmation(db,root/'confirmation',csha,local)
                backup(db,local,root/'backup.zip');restore(root/'backup.zip',root/'restored');other=connect(root/'restored/vault.sqlite3')
                try:self.assertEqual(identity_search(db,q='Person A variant'),identity_search(other,q='Person A variant'));self.assertEqual(protected(db),protected(other));self.assertTrue(validate(other)['ok'])
                finally:other.close()
                with db:db.execute("UPDATE identity_record SET body='{}' WHERE kind='question'")
                self.assertFalse(validate(db)['ok'])
                with self.assertRaises(ValueError):public_projection(db,'github-index')
            finally:db.close()

    def test_direct_word_body_numbering_excludes_nested_table_paragraphs(self):
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Nested</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:r><w:t>Direct</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(direct_paragraphs(raw.getvalue()),[{'location':'P1','xml_body_p':2,'text':'Direct'}])

    def test_http_identity_read_only_and_origin_protection(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db,local,f,sha,_=fixture(root);import_identity(db,root/'identity',sha,local);db.close()
            server=make_server(local/'vault.sqlite3',local,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(server.server_address[1])
            try:
                with urllib.request.urlopen(url+'/api/identity-cards?priority=1') as response:self.assertEqual(json.load(response)['count'],2)
                with self.assertRaises(urllib.error.HTTPError) as ex:urllib.request.urlopen(urllib.request.Request(url+'/api/identity-cards',headers={'Origin':'https://untrusted.invalid'}))
                self.assertEqual(ex.exception.code,403);ex.exception.close()
                with self.assertRaises(urllib.error.HTTPError) as ex:urllib.request.urlopen(urllib.request.Request(url+'/api/identity-cards',data=b'{}'))
                self.assertEqual(ex.exception.code,405);ex.exception.close()
            finally:server.shutdown();server.server_close();thread.join()
