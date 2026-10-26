import copy
import unittest
from p4vault.interview_map import validate_map_record

def fixture():
    return {'state':'local_content_correspondence','publication_scope':'private_research','whole_document_correspondence':False,'whole_media_correspondence':False,'real_speaker_assigned':False,'confirmed_event_count':0,'document':{'document_id':'synthetic-doc','xml_paragraph_count':6,'nonempty_paragraphs':[1,3,4,6],'read_nonempty_count':4},'media':{'source_id':'synthetic-video','kind':'video','runtime_seconds':60,'input_ranges':[[0,10]]},'mapped_paragraphs':[3],'unmapped_nonempty_paragraphs':[1,4,6],'mappings':[{'mapping_id':'synthetic-map','source_id':'synthetic-video','document_id':'synthetic-doc','xml_paragraphs':[3],'source_range':[2,5],'human_listened':False,'speaker_id':None,'permission':'private','limitations':'local sequence only','evidence_id':'synthetic-evidence'}]}

class InterviewMapTest(unittest.TestCase):
    def test_reading_all_paragraphs_still_only_local_correspondence(self):
        r=fixture();self.assertEqual(validate_map_record(r),[])
        r['whole_document_correspondence']=True;self.assertIn('local_promoted_to_whole',validate_map_record(r))

    def test_unlistened_or_incomplete_cannot_be_full_text(self):
        r=fixture();r['state']='full_text_correspondence';r['whole_document_correspondence']=True
        self.assertIn('incomplete_full_text',validate_map_record(r));self.assertIn('unreviewed_full_text',validate_map_record(r))
        r['mappings'][0]['xml_paragraphs']=[1,3,4,6];r['mapped_paragraphs']=[1,3,4,6];r['unmapped_nonempty_paragraphs']=[]
        self.assertIn('unreviewed_full_text',validate_map_record(r))
        r['mappings'][0]['human_listened']=True;r['full_text_verification']='synthetic independent review receipt'
        self.assertEqual(validate_map_record(r),[]);self.assertFalse(r['whole_media_correspondence'])

    def test_wrong_source_numbering_time_identity_and_duplicates_rejected(self):
        for mode in ['wrong_source','wrong_doc','outside_input','zero_duration','nan','blank_paragraph','unmapped','identity','whole_media','duplicate','public']:
            with self.subTest(mode=mode):
                r=copy.deepcopy(fixture());m=r['mappings'][0]
                if mode=='wrong_source':m['source_id']='other'
                elif mode=='wrong_doc':m['document_id']='other'
                elif mode=='outside_input':m['source_range']=[9,11]
                elif mode=='zero_duration':m['source_range']=[2,2]
                elif mode=='nan':m['source_range']=[float('nan'),3]
                elif mode=='blank_paragraph':m['xml_paragraphs']=[2]
                elif mode=='unmapped':r['unmapped_nonempty_paragraphs']=[]
                elif mode=='identity':m['speaker_id']='guessed-person'
                elif mode=='whole_media':r['whole_media_correspondence']=True
                elif mode=='duplicate':r['mappings']*=2
                else:m['permission']='public'
                self.assertTrue(validate_map_record(r))

    def test_audio_missing_and_same_day_are_distinct(self):
        r=fixture();r['state']='audio_only';r['video_correspondence_claimed']=False
        self.assertIn('audio_promoted_to_video',validate_map_record(r));r['media']['kind']='audio';self.assertEqual(validate_map_record(r),[])
        r=fixture();r.update(state='same_day_candidate',mappings=[],mapped_paragraphs=[],unmapped_nonempty_paragraphs=[1,3,4,6]);self.assertEqual(validate_map_record(r),[])
        r['state']='missing';self.assertIn('absence_not_verified',validate_map_record(r));r['state']='unmapped';self.assertEqual(validate_map_record(r),[])

    def test_existing_evidence_must_match_document_source_paragraph_and_range(self):
        import sqlite3,json
        from p4vault.interview_map import validate_interview_maps
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        try:
            db.execute('CREATE TABLE identity_record(id TEXT,kind TEXT,body TEXT)');r=fixture();m=r['mappings'][0]
            original={k:m[k] for k in ('mapping_id','source_id','document_id','xml_paragraphs')};original.update(source_start_seconds=2,source_end_seconds=5)
            db.execute('INSERT INTO identity_record VALUES(?,?,?)',('map','workflow-interview-map',json.dumps({'record':r})))
            db.execute('INSERT INTO identity_record VALUES(?,?,?)',('synthetic-evidence','workflow-evidence',json.dumps({'record':{'original_mapping':original}})))
            self.assertEqual(validate_interview_maps(db),[])
            original['document_id']='unrelated-document';db.execute('UPDATE identity_record SET body=? WHERE id=?',(json.dumps({'record':{'original_mapping':original}}),'synthetic-evidence'))
            self.assertIn('interview_map_evidence_mismatch',[e['code'] for e in validate_interview_maps(db)])
            db.execute("DELETE FROM identity_record WHERE id='synthetic-evidence'");self.assertIn('interview_map_broken_evidence',[e['code'] for e in validate_interview_maps(db)])
        finally:db.close()
