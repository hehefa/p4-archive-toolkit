"""Synthetic archive records only; real source payloads never enter this suite."""
import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from p4vault.core import connect,canonical,file_hash,validate,digest,search
from p4vault.archive import import_archive,archive_excerpt
from p4vault.identity import import_identity,import_confirmation,identity_search,identity_detail,identity_id
from p4vault.review import review_search,review_detail
from p4vault.publish import public_projection
from p4vault.backup import backup,restore
from tests.test_identity import fixture,confirmation,package,protected


def archive_fixture(root):
    db,local,f,sha,rsha=fixture(root);import_identity(db,root/'identity',sha,local);cf,csha=confirmation(root,f,sha,rsha);import_confirmation(db,root/'confirmation',csha,local)
    lines=['An attributed description of a staged movement, not independent proof of audience effects.','A source proposes an end-of-day discussion; no adoption or causal chain is established.','Local alias (Person A variant)','Scoped recorder asks about the stage.','Synthetic person responds about a stage experience.','Other alias (Scoped speaker)']
    primary=root/'article.md';primary.write_text('\n'.join(lines)+'\n');mirror=root/'mirror.md';mirror.write_text('Different publication wrapper\n'+'\n'.join(lines)+'\n')
    xmltext='An undated organizational text reports discussions; it does not identify an initiator.'
    doc=root/'organizational.docx'
    with zipfile.ZipFile(doc,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/><w:p><w:r><w:t>'+xmltext+'</w:t></w:r></w:p></w:body></w:document>')
    sources=[{'id':'primary','path':str(primary),'sha256':file_hash(primary),'scope_read':'L1–6','type':'edited interview','publication':'source publication field only'},{'id':'mirror','path':str(mirror),'sha256':file_hash(mirror),'scope_read':'matched long prose','type':'same-text publication','publication':'different publication wrapper'},{'id':'pr','path':str(doc),'sha256':file_hash(doc),'scope_read':'XML body P2','type':'previously read organizational statement','publication':'unknown'}]
    people=[{'id':'person','name':'Synthetic person','status':'same-project source card','basis':'explicit source ID'},{'id':'person-b','name':'Person B','status':'source card','basis':'source attribution'},{'id':'DOC158-g','name':'Scoped speaker','existing_same_name_candidate':'person-c','status':'source position only','basis':'source signature'},{'id':'DOC158-w','name':'Scoped recorder','existing_same_name_candidate':None,'status':'source position only','basis':'source question and credit'}]
    assignments=[(['person'],['Synthetic person'],['L1']),(['DOC158-g'],['Scoped speaker'],['L2']),(['person'],['Synthetic person'],['L3']),(['person-b'],['Person B'],['L1']),(['DOC158-w','person'],['Scoped recorder','Synthetic person'],['L4','L5']),(['DOC158-g','person'],['Scoped speaker','Synthetic person'],['primary L2','pr XML body P2'])]
    rows=[]
    for i,(ids,names,anchors) in enumerate(assignments,1):rows.append({'id':f'NEW158-{i:02}','event_id':f'EV158-{i:02}','project':'Synthetic project','title':f'Synthetic process record {i}','people_ids':ids,'people_labels':names,'claim':'Source-bound synthetic process statement','source_ids':['primary','pr'] if i==6 else ['primary'],'anchors':anchors,'evidence_status':'source statement with limits','event_date':None,'publication_date':'2020-01-02','uncertainty':'Not a new event or independent implementation proof','newness':'new structured reading' if i<6 else 'new and old source comparison','visibility':'local_private','biometric_identity':False})
    aliases=[{'alias':'Local alias','person_id':'person','canonical':'Synthetic person','source_id':'primary','anchor':'L3','explicit_text':lines[2],'user_confirmation':'c0','scope':'this manuscript only'},{'alias':'Other alias','document_name':'Scoped speaker','source_position_id':'DOC158-g','source_id':'primary','anchor':'L6','explicit_text':lines[5],'scope':'this manuscript only'}]
    d={'schema':'p4.private.archive-increment/158','idempotency_key':'synthetic-process','source_registry':sources,'added_records':rows,'source_scoped_people':people,'aliases_file':'别名补证_限定来源.json','protected_base_manifests':{'review':rsha,'identity':sha,'confirmation':csha},'base89_sha256':f['delta_156_身份参与侧表.json']['base89_sha256'],'replaced_records':[],'removed_records':[],'global_identity_merges':[],'visibility':'local_private','event_count_warning':'Six process records, not six new events'}
    a=[{'source_id':'primary','anchor':'L'+str(i),'text':line} for i,line in enumerate(lines,1)]+[{'source_id':'pr','anchor':'XML body P2','text':xmltext}]
    files={'delta_158_系统研究增量.json':d,'六项过程档案.json':rows,'来源与阅读范围.json':sources,'人物署名位置与身份边界.json':people,'精确原文锚点_私有.json':a,'别名补证_限定来源.json':aliases,'去重与排除记录.json':{'mirror_long_prose_identical':{'matched':2,'total':2}},'刊载图来源.json':[]}
    archive_sha=package(root/'archive',files)
    return db,local,files,archive_sha,(f,sha,cf,csha)


class ArchiveTest(unittest.TestCase):
    def test_append_preserves_old_layers_and_scoped_alias_does_not_become_global(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,f,sha,prior=archive_fixture(root)
            try:
                old=protected(db);identity=[tuple(r) for r in db.execute('SELECT * FROM identity_record')]
                result=import_archive(db,root/'archive',sha,local);self.assertEqual(result['records'],6);self.assertEqual(result['new_graph_events'],0)
                for table,rows in old.items():self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')][:len(rows)],rows)
                self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM identity_record')][:len(identity)],identity)
                self.assertEqual(identity_search(db,q='Local alias')['count'],1);self.assertEqual(search(db,'Local alias',kind='person'),[])
                self.assertEqual(identity_search(db)['count'],3);self.assertEqual(review_search(db)['count'],7)
                self.assertEqual(review_search(db,query='Other alias')['count'],2);self.assertEqual(search(db,'Other alias',kind='person'),[])
                qa=review_search(db,query='NEW158-05')['records'][0];b=review_detail(db,qa['id'])['body'];self.assertTrue(b['direct_dialogue_record']);self.assertEqual(b['source_direction_labels']['from_person'],'Scoped recorder');self.assertEqual(b['mapping']['unresolved'][0]['reference'],'DOC158-w')
                compare=review_detail(db,review_search(db,query='NEW158-06')['records'][0]['id'])['body'];self.assertFalse(compare['archive_context']['causal_connection_confirmed']);self.assertEqual(compare['archive_context']['additional_source_evidence'][0]['anchor'],'XML body P2');self.assertEqual(compare['archive_context']['source_family']['matched_long_paragraphs'],2)
                self.assertEqual(import_archive(db,root/'archive',sha,local)['status'],'already_imported')
                self.assertEqual(import_identity(db,root/'identity',prior[1],local)['status'],'already_imported')
                self.assertEqual(import_confirmation(db,root/'confirmation',prior[3],local)['status'],'already_imported')
                self.assertTrue(validate(db)['ok']);self.assertEqual(public_projection(db,'github-index')['count'],0)
            finally:db.close()

    def test_rejects_source_mirror_alias_or_anchor_drift_before_any_new_rows(self):
        for mode in ['mirror','alias','anchor','date','base']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);db,local,f,sha,_=archive_fixture(root)
                try:
                    if mode=='mirror':
                        path=Path(f['来源与阅读范围.json'][1]['path']);path.write_text('Different content');f['来源与阅读范围.json'][1]['sha256']=file_hash(path)
                    elif mode=='alias':f['别名补证_限定来源.json'][0]['person_id']='person-b'
                    elif mode=='anchor':f['精确原文锚点_私有.json'][-1]['text']='False source quote'
                    elif mode=='date':f['六项过程档案.json'][0]['event_date']='2020-01-02'
                    else:f['delta_158_系统研究增量.json']['base89_sha256']='0'*64
                    sha=package(root/'archive',f);before=protected(db);count=db.execute('SELECT count(*) FROM identity_record').fetchone()[0]
                    with self.assertRaises(ValueError):import_archive(db,root/'archive',sha,local)
                    self.assertEqual(before,protected(db));self.assertEqual(db.execute('SELECT count(*) FROM identity_record').fetchone()[0],count)
                finally:db.close()

    def test_append_rolls_back_review_rows_when_identity_id_collides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,f,sha,_=archive_fixture(root)
            try:
                # A pre-existing unrelated private position must not cause half an import.
                from p4vault.identity import _persist,_record,_persons
                from p4vault.review import _member_payloads
                collision_sha=package(root/'collision',{'evidence.json':{'synthetic':True}})
                payload,manifest=_member_payloads(root/'collision',collision_sha)
                collision=_record('archive-person','person',{'name':'Unrelated scoped position'},_persons(db),person='person')
                _persist(db,root/'collision',collision_sha,payload,manifest,local,[collision],{'schema':'synthetic-only'})
                self.assertTrue(validate(db)['ok'])
                before=protected(db)
                with self.assertRaises(ValueError):import_archive(db,root/'archive',sha,local)
                self.assertEqual(before,protected(db))
            finally:db.close()

    def test_full_backup_restore_and_navigation_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,f,sha,_=archive_fixture(root)
            try:
                import_archive(db,root/'archive',sha,local);backup(db,local,root/'backup.zip');restore(root/'backup.zip',root/'restored');other=connect(root/'restored/vault.sqlite3')
                try:self.assertEqual(review_search(other),review_search(db));self.assertEqual(identity_detail(other,identity_id('archive-person','DOC158-g')),identity_detail(db,identity_id('archive-person','DOC158-g')));self.assertTrue(validate(other)['ok'])
                finally:other.close()
                id=review_search(db,query='NEW158-06')['records'][0]['id'];row=db.execute('SELECT body FROM review_record WHERE id=?',(id,)).fetchone();body=json.loads(row[0]);body['archive_context']['causal_connection_confirmed']=True
                with db:db.execute('UPDATE review_record SET body=?,digest=? WHERE id=?',(canonical(body),digest(body),id))
                self.assertFalse(validate(db)['ok'])
                with self.assertRaises(ValueError):public_projection(db,'github-index')
            finally:db.close()
