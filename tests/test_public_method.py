"""Synthetic editions: no live publication data or private research contents."""
import copy,json,tempfile,unittest
from pathlib import Path
from p4vault.core import digest,canonical,file_hash,connect
from p4vault.public_method import validate_payload,paragraphs,projection_digest,check_method_indexes
from p4vault.workflow import import_workflow,workflow_detail,import_receipt
from p4vault.adjacent import import_adjacent
from p4vault.identity import identity_id
from p4vault.backup import backup,restore
from tests.test_workflow import workflow_fixture


def payload():
    s={'id':'synthetic-source','title':'Synthetic public source','body':'An announced plan.','boundary':'Not implementation evidence','project_slug':None,'url':'https://p4theater.cn/articles/synthetic/'};s['snapshotVersion']=digest(s)
    a={'id':'synthetic-method','kind':'public_method','title':'合成公开方法','title_en':'Synthetic method','body':'## 合成标题\n\n合成计划。[S1]\n\n方法比较。[R1]','body_en':'## Synthetic heading\n\nA plan. [S1]\n\nA comparison. [R1]','references':[{'label':'S1','sourceId':s['id'],'sourceVersion':s['snapshotVersion'],'url':s['url']}],'readingReferences':[{'label':'R1','studyId':'SYNTHETIC-STUDY','version':'v1','url':'/research/event-plans/synthetic-study','relation':'method_comparison_not_causal'}]};a['articleVersion']=digest(a)
    urls={lang:'https://b.p4theater.cn'+('/en' if lang=='en' else '')+'/research/'+a['id'] for lang in ('zh','en')};urls.update({'fixed_'+lang:urls[lang]+'/versions/'+a['articleVersion'] for lang in ('zh','en')})
    return {'schema':'p4_public_method_essay_v1','status':'production_verified','commit':'c'*40,'deployment':'synthetic-deploy','article':a,'sources':[s],'urls':urls,'new_articles':1,'new_public_sources':1,'new_event_reconstructions':0,'new_videos':0,'new_images':0,'new_projects':0}


def indexed(p):
    a=p['article'];docs={'entities':[{'id':'comparison'}]};index={'records':[]}
    docs['entities'].append({'id':'source','text':p['sources'][0]['body']});index['records'].append({'id':'source','public_method_source':{'original_url':p['sources'][0]['url'],'snapshot_version':p['sources'][0]['snapshotVersion']}})
    for lang in ('zh','en'):
        body=a['body_en' if lang=='en' else 'body'];docs['entities'].append({'id':lang,'text':body,'public_url':p['urls'][lang],'refs':['source','comparison']});index['records'].append({'id':lang,'public_method':{'article_id':a['id'],'article_version':a['articleVersion'],'language':lang,'fixed_url':p['urls']['fixed_'+lang],'paragraphs':paragraphs(body,lang),'references':[{'label':'S1','document_id':'source','kind':'source','version':p['sources'][0]['snapshotVersion'],'original_url':p['sources'][0]['url']},{'label':'R1','document_id':'comparison','kind':'comparison','relation':'method_comparison_not_causal'}]}})
    approval={'article_id':a['id'],'article_version':a['articleVersion'],'document_ids':['zh','en'],'source_document_ids':['source'],'destination':'github:synthetic/archive','permission_basis':'explicit_user_approved','projection_sha256':projection_digest(docs,index,['zh','en','source'])};scope={'repository':'synthetic/archive','approved_public_methods':[approval]};return docs,index,scope


class PublicMethodTest(unittest.TestCase):
    def test_bilingual_paragraphs_and_source_editions_are_stable(self):
        p=payload();self.assertEqual(validate_payload(p),p['article']);d,i,s=indexed(p);self.assertEqual(check_method_indexes(d,i,s),[]);self.assertEqual(paragraphs(p['article']['body'],'zh')[1]['labels'],['S1']);self.assertNotEqual(paragraphs('same','zh')[0]['id'],paragraphs('same','en')[0]['id'])

    def test_payload_rejects_revision_scope_duplicates_and_broken_citations(self):
        for kind in ['hash','media','duplicate','citation','source','fixed','causal','extra']:
            with self.subTest(kind=kind):
                p=payload()
                if kind=='extra':p['internal_notes']='unapproved extra field'
                if kind=='hash':p['article']['body']+=' changed'
                if kind=='media':p['new_videos']=1
                if kind=='duplicate':p['sources'].append(copy.deepcopy(p['sources'][0]));p['new_public_sources']=2
                if kind=='citation':p['article']['body']+=' [S9]'
                if kind=='source':p['sources'][0]['body']='Changed source'
                if kind=='fixed':p['urls']['fixed_en']=p['urls']['en']
                if kind=='causal':p['article']['readingReferences'][0]['relation']='influenced'
                with self.assertRaises(ValueError):validate_payload(p)

    def test_index_rejects_unapproved_or_broken_paragraph_and_source_links(self):
        for kind in ['grant','paragraph','reference','version','language','unapproved']:
            with self.subTest(kind=kind):
                d,i,s=indexed(payload());m=i['records'][1]['public_method']
                if kind=='grant':s['approved_public_methods'][0]['projection_sha256']='0'*64
                if kind=='paragraph':m['paragraphs'].append(copy.deepcopy(m['paragraphs'][0]))
                if kind=='reference':m['references'][0]['document_id']='missing'
                if kind=='version':m['references'][0]['version']='wrong'
                if kind=='language':i['records'][2]['public_method']['language']='zh'
                if kind=='unapproved':s['approved_public_methods']=[]
                if kind not in {'grant','unapproved'}:s['approved_public_methods'][0]['projection_sha256']=projection_digest(d,i,['zh','en','source'])
                self.assertTrue(check_method_indexes(d,i,s))

    def test_edited_receipt_exact_binding_idempotence_private_boundary_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db,local,f,sha=workflow_fixture(root);other=None
            try:
                import_adjacent(db,root/'adjacent',sha,local);import_workflow(db,root/'adjacent',sha,local);id=identity_id('workflow-article','research-162/method-candidate');d=workflow_detail(db,id);private=workflow_detail(db,identity_id('workflow-article','research-162/private-narrative'));p=payload();pp=root/'public.json';pp.write_text(canonical(p));ap=root/'approval.json';review={'article_id':p['article']['id'],'article_version':p['article']['articleVersion'],'private_full_article_read':False,'private_video_or_machine_text_read':False,'allowed_images':[],'excluded_claims':[r['id'] for r in d['body']['record']['blocked_statements']],'manifest_sha256':sha};ap.write_text(canonical(review));rp=root/'readback.html';rp.write_text('<p>Synthetic published edition</p>')
                receipt={'schema':'p4.workflow.publication-receipt/1','document_id':id,'document_digest':d['digest'],'scope':'method_only','publisher':'solewriter','approval_evidence_path':str(ap),'approval_evidence_sha256':file_hash(ap),'url':p['urls']['fixed_zh'],'deployment_id':p['deployment'],'published_content_sha256':file_hash(rp),'readback_path':str(rp),'readback_at':'2026-01-01T00:00:00+00:00','checks':{'links_verified':True,'method_only':True,'private_material_excluded':True},'public_edition':{'payload_path':str(pp),'payload_sha256':file_hash(pp),'article_id':p['article']['id'],'article_version':p['article']['articleVersion'],'relation':'public_editorial_revision_of_candidate'},'published_article':p['article'],'published_urls':p['urls']};rfile=root/'receipt.json'
                for field,value in [('published_article',{**p['article'],'body':'Wrong version'}),('public_edition',{**receipt['public_edition'],'payload_sha256':'0'*64})]:
                    bad={**receipt,field:value};rfile.write_text(canonical(bad))
                    with self.assertRaises(ValueError):import_receipt(db,local,id,rfile,file_hash(rfile),d['state_token'])
                wrong={**review,'manifest_sha256':'0'*64};ap.write_text(canonical(wrong));bad={**receipt,'approval_evidence_sha256':file_hash(ap)};rfile.write_text(canonical(bad))
                with self.assertRaises(ValueError):import_receipt(db,local,id,rfile,file_hash(rfile),d['state_token'])
                ap.write_text(canonical(review));rfile.write_text(canonical(receipt));updated=import_receipt(db,local,id,rfile,file_hash(rfile),d['state_token']);self.assertEqual(updated['state'],'published_verified');self.assertEqual(updated['body'],d['body']);self.assertEqual(workflow_detail(db,private['id']),private)
                count=db.execute('SELECT count(*) FROM identity_record').fetchone()[0];self.assertEqual(import_receipt(db,local,id,rfile,file_hash(rfile),d['state_token']),updated);self.assertEqual(db.execute('SELECT count(*) FROM identity_record').fetchone()[0],count)
                backup(db,local,root/'backup.zip');restore(root/'backup.zip',root/'restored');other=connect(root/'restored/vault.sqlite3');self.assertEqual(workflow_detail(other,id),updated);self.assertTrue(list((root/'restored/research_snapshots').glob('*/published-payload.json')))
            finally:
                if other:other.close()
                db.close()
