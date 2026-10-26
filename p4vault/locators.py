"""Private location candidates linked by explicit IDs. Never replace source paths."""
import hashlib
import json
import math
from pathlib import PurePosixPath

ALGORITHM='sha256(offset_ascii_nul_1MiB_at_start_middle_end)'
def _rows(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_record'").fetchone():return []
    return [(r['id'],json.loads(r['body'])['record']) for r in db.execute("SELECT id,body FROM identity_record WHERE kind='workflow-evidence'")]

def validate_binding(db,r):
    b=r.get('locator_binding')
    if b is None:return []
    errors=[]
    def require(ok,code):
        if not ok:errors.append(code)
    try:
        require(b['status']=='candidate' and b['permission']=='private','candidate_scope')
        require(b['full_hash_verified'] is False and b['new_research'] is False and b['human_listened'] is False,'overclaim')
        require(len(b['core_entity_ids'])==len(set(b['core_entity_ids'])),'duplicate_core_binding')
        require(isinstance(b['old_source_id'],str) and bool(b['old_source_id']),'missing_source_id')
        if b['type']=='source':
            sample=b['sample'];size=b['bytes'];require(type(size) is int and size>3*1048576,'invalid_size')
            require(sample['algorithm']==ALGORITHM and sample['region_bytes']==1048576 and sample['offsets']==[0,size//2-524288,size-1048576],'sample_scope')
            require(sample['current_sha256']==sample['backup_sha256'] and len(bytes.fromhex(sample['current_sha256']))==32,'sample_mismatch')
            require(b['camera_xml']['current_sha256']==b['camera_xml']['backup_sha256'] and len(bytes.fromhex(b['camera_xml']['current_sha256']))==32,'camera_xml_mismatch')
            for key in ['old_path','current_path','backup_path']:require(PurePosixPath(b[key]).is_absolute(),'relative_locator')
            require(b['current_path']!=b['old_path'] and b['current_locator_id']!=b['old_source_id'],'path_identity_merge')
            require(type(b['duration_seconds']) in (int,float) and math.isfinite(b['duration_seconds']) and b['duration_seconds']>0,'invalid_duration')
        elif b['type']=='window':
            source=next((r2['locator_binding'] for id,r2 in _rows(db) if id==b['source_locator_id']),None)
            require(source is not None and source.get('type')=='source' and source.get('old_source_id')==b['old_source_id'],'wrong_source_locator')
            a,z=b['source_range'];require(all(type(v) in (int,float) and math.isfinite(v) for v in [a,z]) and 0<=a<z,'window_timing')
            if source:require(z<=source['duration_seconds'],'window_outside_source')
            require(len(bytes.fromhex(b['document_sha256']))==32 and bool(b['upstream_document_id']),'document_version')
        else:require(False,'unknown_binding_type')
        for id in b['core_entity_ids']:
            row=db.execute('SELECT body FROM entity WHERE id=?',(id,)).fetchone();require(row is not None,'missing_core_entity')
            if not row:continue
            e=json.loads(row['body']);m=e.get('metadata',{})
            if b['type']=='source':require(e['kind']=='asset' and e['id']==b['old_source_id'] and m.get('local_path')==b['old_path'],'source_id_binding')
            else:require(e['kind']=='document' and m.get('upstream_id')==b['upstream_document_id'] and b['old_source_id'] in e.get('refs',[]) and [m.get('window_start'),m.get('window_end')]==b['source_range'] and m.get('content_sha256')==b['document_sha256'],'document_id_binding')
    except (KeyError,TypeError,ValueError,StopIteration,OverflowError):errors.append('invalid_structure')
    return sorted(set(errors))

def validate_locators(db):return [{'code':'locator_'+code,'id':id} for id,r in _rows(db) for code in validate_binding(db,r)]

def private_locators(db,entity_id):
    return [{'id':id,'title':r.get('title',id),'type':r['locator_binding']['type'],'status':'candidate'} for id,r in _rows(db) if entity_id in r.get('locator_binding',{}).get('core_entity_ids',[]) and not validate_binding(db,r)]

def entity_navigation(db,r):
    if 'locator_binding' not in r:return []
    if validate_binding(db,r):raise ValueError('invalid private locator binding')
    return [{'id':id,'title':json.loads(db.execute('SELECT body FROM entity WHERE id=?',(id,)).fetchone()[0])['title']} for id in r['locator_binding']['core_entity_ids']]
