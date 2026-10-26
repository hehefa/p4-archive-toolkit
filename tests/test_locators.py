import copy,json,sqlite3,unittest
from p4vault.locators import ALGORITHM,validate_binding,validate_locators,private_locators,entity_navigation

class LocatorTest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE entity(id TEXT,body TEXT)');self.db.execute('CREATE TABLE identity_record(id TEXT,kind TEXT,body TEXT)')
        self.asset={'id':'old-source','kind':'asset','title':'clip','metadata':{'local_path':'/synthetic/old.mp4'}}
        self.doc={'id':'document-one','kind':'document','title':'old window','refs':['old-source'],'metadata':{'upstream_id':'upstream-document','window_start':10,'window_end':20,'content_sha256':'a'*64}}
        for e in [self.asset,self.doc]:self.db.execute('INSERT INTO entity VALUES(?,?)',(e['id'],json.dumps(e)))
        common={'status':'candidate','permission':'private','full_hash_verified':False,'new_research':False,'human_listened':False,'old_source_id':'old-source'}
        self.source={'title':'position candidate','locator_binding':{**common,'type':'source','core_entity_ids':['old-source'],'old_path':'/synthetic/old.mp4','current_path':'/synthetic/current.mp4','backup_path':'/synthetic/backup.mp4','current_locator_id':'new-path-id','bytes':10*1048576,'duration_seconds':60,'sample':{'algorithm':ALGORITHM,'region_bytes':1048576,'offsets':[0,10*1048576//2-524288,9*1048576],'current_sha256':'b'*64,'backup_sha256':'b'*64},'camera_xml':{'current_sha256':'c'*64,'backup_sha256':'c'*64}}}
        self.window={'title':'retained old window','locator_binding':{**common,'type':'window','core_entity_ids':['document-one'],'source_locator_id':'source-card','upstream_document_id':'upstream-document','document_sha256':'a'*64,'source_range':[10,20]}}
        self.save('source-card',self.source);self.save('window-card',self.window)
    def save(self,id,r):self.db.execute('DELETE FROM identity_record WHERE id=?',(id,));self.db.execute('INSERT INTO identity_record VALUES(?,?,?)',(id,'workflow-evidence',json.dumps({'record':r})))
    def tearDown(self):self.db.close()
    def test_explicit_links_are_bidirectional_without_rewriting_entities(self):
        before=list(self.db.execute('SELECT * FROM entity'));self.assertEqual(validate_locators(self.db),[])
        self.assertEqual(private_locators(self.db,'document-one')[0]['id'],'window-card');self.assertEqual(entity_navigation(self.db,self.window),[{'id':'document-one','title':'old window'}]);self.assertEqual(list(self.db.execute('SELECT * FROM entity')),before)
    def test_sample_scope_xml_and_no_full_hash_promotion(self):
        for mode in ['full','research','listened','public','sample','offsets','xml','merge']:
            r=copy.deepcopy(self.source);b=r['locator_binding']
            if mode=='full':b['full_hash_verified']=True
            elif mode=='research':b['new_research']=True
            elif mode=='listened':b['human_listened']=True
            elif mode=='public':b['permission']='public'
            elif mode=='sample':b['sample']['backup_sha256']='d'*64
            elif mode=='offsets':b['sample']['offsets']=[0,999]
            elif mode=='xml':b['camera_xml']['backup_sha256']='d'*64
            else:b['current_path']=b['old_path']
            self.assertTrue(validate_binding(self.db,r),mode)
    def test_wrong_document_source_version_or_range_rejected(self):
        for field,value in [('upstream_document_id','another'),('old_source_id','other-source'),('document_sha256','d'*64),('source_range',[11,20]),('core_entity_ids',['missing'])]:
            r=copy.deepcopy(self.window);r['locator_binding'][field]=value;self.assertTrue(validate_binding(self.db,r),field)
    def test_unmigrated_legacy_window_stays_explicit_bridge(self):
        r=copy.deepcopy(self.window);r['locator_binding'].update(core_entity_ids=[],upstream_document_id='not-yet-imported');self.assertEqual(validate_binding(self.db,r),[]);self.assertEqual(entity_navigation(self.db,r),[])
    def test_timing_bounds_and_missing_source_rejected(self):
        for span in [[20,10],[10,10],[-1,2],[10,61],[float('nan'),20]]:
            r=copy.deepcopy(self.window);r['locator_binding']['source_range']=span;self.assertTrue(validate_binding(self.db,r))
        self.db.execute("DELETE FROM identity_record WHERE id='source-card'");self.assertIn('wrong_source_locator',validate_binding(self.db,self.window))
    def test_same_name_does_not_create_navigation(self):
        other=copy.deepcopy(self.doc);other['id']='unrelated-same-name';self.db.execute('INSERT INTO entity VALUES(?,?)',(other['id'],json.dumps(other)));self.assertEqual(private_locators(self.db,other['id']),[])
