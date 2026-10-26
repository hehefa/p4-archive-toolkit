"""Synthetic manuscripts only; source labels never imply global identity."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape
from p4vault.core import canonical,connect,file_hash,validate
from p4vault.process import import_process
from p4vault.archive import import_archive
from p4vault.review import review_detail,review_search
from p4vault.identity import identity_id,identity_detail
from p4vault.backup import backup,restore
from p4vault.publish import public_projection
from tests.test_identity import package,protected
from tests.test_archive import archive_fixture


def write_package(root,files):
    sha=package(root,files)
    (root/'完整阶段研究_提议与原则回应_私有.md').write_text('# 《Synthetic project》\nSelected work stages only.\n')
    manifest=json.loads((root/'MANIFEST.json').read_text());manifest['完整阶段研究_提议与原则回应_私有.md']=file_hash(root/'完整阶段研究_提议与原则回应_私有.md')
    (root/'MANIFEST.json').write_text(canonical(manifest));return file_hash(root/'MANIFEST.json')


def process_fixture(root):
    db,local,af,asha,_=archive_fixture(root);import_archive(db,root/'archive',asha,local)
    paragraphs=['Stage work question '+str(i) for i in range(1,6)]
    paragraphs[2]='A work proposal. PRIVATE_OMITTED_SENTINEL unrelated personal material.'
    def doc(path,lines):
        xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/>'+''.join('<w:p><w:r><w:t>'+escape(t)+'</w:t></w:r></w:p>' for t in lines)+'</w:body></w:document>'
        with zipfile.ZipFile(path,'w') as z:z.writestr('word/document.xml',xml)
    primary=root/'20200101-stage.docx';old=root/'20201030-recheck.docx';doc(primary,paragraphs);doc(old,['Prior studied work question'])
    family='synthetic-manuscript';sources=[{'source_id':'synthetic-primary','family_id':family,'path':str(primary),'sha256':file_hash(primary),'date':'filename hint only','novelty':'new structured work stages'},{'source_id':'synthetic-recheck','path':str(old),'sha256':file_hash(old)}]
    positions=[{'id':'DOC159-'+str(i),'label':'Scoped speaker '+str(i),'canonical_person_id':None,'scope':family} for i in range(4)]
    rows=[{'id':f'NEW159-{i:02}','event_group_id':'synthetic-one-group','source_id':sources[0]['source_id'],'source_family':family,'new_event_count':0,'event_date':None,'visibility':'local_private','paragraphs':[i],'participant_position_ids':[positions[(i-1)%4]['id']],'participants':[positions[(i-1)%4]['label']],'type':'documented stage','claim':'Selected work statement','limit':'Not implementation proof','date_hint':'Unverified filename','title':'Stage '+str(i)} for i in range(1,6)]
    anchors=[{'P':i,'xml_body_p':i+1,'text':t,'is_full_paragraph':True} for i,t in enumerate(paragraphs,1)]
    anchors[2]={'P':3,'xml_body_p':4,'text':'Editorial omission; retain work proposal only.','is_full_paragraph':False,'exact_excerpt':'A work proposal.'}
    candidates=[{'raw':'P1/P2','published':'primary L1/L2','status':'candidate only'},{'raw':'P3','published':'primary L3','status':'candidate only'},{'raw':'20201030 P1','published':'primary L4/L5','status':'previously studied, not new'}]
    report=root/'old-report.md';report.write_text('Prior work questions were already studied.')
    supplement={'relations':candidates,'old135_report':str(report),'note':'Append scope clarification; preserve earlier records.'}
    d={'schema':'p4.private.process-increment/159','visibility':'local_private','idempotency_key':family,'new_confirmed_whole_events':0,'replaced_records':[],'global_identity_merges':[],'base89_sha256':af['delta_158_系统研究增量.json']['base89_sha256'],'protected':dict(af['delta_158_系统研究增量.json']['protected_base_manifests'],archive=asha),'added_stage_records':rows,'document_positions':positions,'sources':sources[:1],'version_relation_candidates':candidates}
    files={'delta_159_独立研究增量.json':d,'来源登记.json':sources,'五阶段问答记录.json':rows,'文稿参与位置_未强配实名.json':positions,'工作过程锚点_私有.json':anchors,'版本对应与158增量口径补充.json':supplement,'核验结果.json':{'new_doc_nonempty_paragraphs':5,'old_doc_rechecked_paragraphs':1}}
    sha=write_package(root/'process',files);return db,local,files,sha,asha


class ProcessTest(unittest.TestCase):
    def test_one_group_scoped_people_partial_excerpt_overlays_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,f,sha,asha=process_fixture(root)
            try:
                before=protected(db);old_identity=[tuple(r) for r in db.execute('SELECT * FROM identity_record ORDER BY rowid')]
                result=import_process(db,root/'process',sha,local)
                self.assertEqual((result['records'],result['identity_navigation_records'],result['partial_paragraphs']),(5,12,1))
                for table,rows in before.items():self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid')][:len(rows)],rows)
                self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM identity_record ORDER BY rowid')][:len(old_identity)],old_identity)
                rows=review_search(db,query='NEW159-')['records'];self.assertEqual(len(rows),5)
                bodies=[review_detail(db,r['id'])['body'] for r in rows];self.assertEqual(len({b['record']['event'] for b in bodies}),1)
                for b in bodies:self.assertEqual(b['mapping']['links'],[]);self.assertFalse(b['archive_context']['global_identity_confirmed'])
                b=next(b for b in bodies if b['record']['id']=='NEW159-03');self.assertEqual(b['source_evidence']['lines'][0]['text'],'A work proposal.');self.assertFalse(b['source_evidence']['lines'][0]['is_full_paragraph'])
                for table in ['review_record','review_revision','identity_record','identity_revision']:
                    self.assertNotIn('PRIVATE_OMITTED_SENTINEL',' '.join(str(tuple(r)) for r in db.execute('SELECT * FROM '+table)))
                for path in (local/'research_snapshots').rglob('*'):
                    if path.is_file():self.assertNotIn(b'PRIVATE_OMITTED_SENTINEL',path.read_bytes())
                oldid=review_search(db,query='NEW158-01')['records'][0]['id'];overlays=review_detail(db,oldid)['private_overlays'];self.assertEqual({r['kind'] for r in overlays},{'process-version','archive-amendment'})
                self.assertEqual(import_process(db,root/'process',sha,local)['status'],'already_imported')
                self.assertEqual(import_archive(db,root/'archive',asha,local)['status'],'already_imported')
                self.assertTrue(validate(db)['ok']);self.assertEqual(public_projection(db,'github-index')['count'],0)
            finally:db.close()

    def test_rejects_source_anchor_identity_event_or_dependency_drift_atomically(self):
        for mode in ['hash','xml','partial','identity','date','group','dependency','published']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);db,local,f,sha,_=process_fixture(root)
                try:
                    if mode=='hash':f['来源登记.json'][0]['sha256']='0'*64
                    elif mode=='xml':f['工作过程锚点_私有.json'][0]['xml_body_p']=1
                    elif mode=='partial':f['工作过程锚点_私有.json'][2]['exact_excerpt']='Fabricated quote'
                    elif mode=='identity':f['文稿参与位置_未强配实名.json'][0]['canonical_person_id']='person'
                    elif mode=='date':f['五阶段问答记录.json'][0]['event_date']='2020-01-01'
                    elif mode=='group':f['五阶段问答记录.json'][0]['event_group_id']='second-group'
                    elif mode=='dependency':f['delta_159_独立研究增量.json']['protected']['archive']='0'*64
                    else:f['版本对应与158增量口径补充.json']['relations'][0]['published']='primary L99'
                    sha=write_package(root/'process',f);before=protected(db);n=db.execute('SELECT count(*) FROM identity_record').fetchone()[0]
                    with self.assertRaises(ValueError):import_process(db,root/'process',sha,local)
                    self.assertEqual(before,protected(db));self.assertEqual(db.execute('SELECT count(*) FROM identity_record').fetchone()[0],n)
                finally:db.close()

    def test_backup_restores_all_navigation_and_detects_false_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,f,sha,_=process_fixture(root)
            try:
                import_process(db,root/'process',sha,local);backup(db,local,root/'backup.zip');restore(root/'backup.zip',root/'restored');other=connect(root/'restored/vault.sqlite3')
                try:
                    self.assertEqual(review_search(other),review_search(db));self.assertTrue(validate(other)['ok'])
                    for kind,key in [('process-group','synthetic-one-group'),('process-person','DOC159-0'),('process-version','synthetic-manuscript/version/1'),('archive-amendment','AMEND159-NOVELTY158')]:self.assertEqual(identity_detail(other,identity_id(kind,key)),identity_detail(db,identity_id(kind,key)))
                finally:other.close()
                id=review_search(db,query='NEW159-01')['records'][0]['id'];body=json.loads(db.execute('SELECT body FROM review_record WHERE id=?',(id,)).fetchone()[0]);body['archive_context']['implementation_confirmed']=True
                from p4vault.core import digest
                with db:db.execute('UPDATE review_record SET body=?,digest=? WHERE id=?',(canonical(body),digest(body),id))
                self.assertFalse(validate(db)['ok'])
            finally:db.close()
