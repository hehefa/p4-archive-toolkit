"""Private document/media correspondence. Reading coverage never implies alignment."""
import json
import math

STATES={'unmapped','same_day_candidate','local_content_correspondence','full_text_correspondence','audio_only','missing'}

def validate_map_record(r):
    errors=[]
    def require(ok,code):
        if not ok:errors.append(code)
    try:
        state=r['state'];rows=r['mappings'];doc=r['document'];media=r['media']
        require(state in STATES,'unknown_state')
        require(r['whole_media_correspondence'] is False,'whole_media_overclaim')
        require(r['real_speaker_assigned'] is False and r['confirmed_event_count']==0,'identity_or_event_overclaim')
        require(r['publication_scope']=='private_research','publication_scope')
        all_paragraphs=doc['nonempty_paragraphs'];mapped=sorted({p for x in rows for p in x['xml_paragraphs']})
        require(len(all_paragraphs)==len(set(all_paragraphs)) and all(type(p) is int and 1<=p<=doc['xml_paragraph_count'] for p in all_paragraphs),'document_numbering')
        require(type(doc['read_nonempty_count']) is int and 0<=doc['read_nonempty_count']<=len(all_paragraphs),'read_count')
        require(set(mapped)<=set(all_paragraphs) and r['mapped_paragraphs']==mapped,'mapped_paragraphs')
        require(r['unmapped_nonempty_paragraphs']==sorted(set(all_paragraphs)-set(mapped)),'unmapped_paragraphs')
        require(len({x['mapping_id'] for x in rows})==len(rows),'duplicate_mapping')
        for x in rows:
            a,b=x['source_range'];duration=media['runtime_seconds']
            require(all(type(v) in (int,float) and math.isfinite(v) for v in [a,b,duration]) and 0<=a<b<=duration,'source_timing')
            require(any(lo<=a and b<=hi for lo,hi in media['input_ranges']),'outside_read_input')
            require(x['source_id']==media['source_id'] and x['document_id']==doc['document_id'],'source_document_mismatch')
            require(x['speaker_id'] is None and x['permission']=='private','mapping_identity_or_permission')
            require(bool(x['xml_paragraphs']) and bool(x['limitations']),'unbounded_mapping')
        if state=='local_content_correspondence':require(bool(rows) and r['whole_document_correspondence'] is False,'local_promoted_to_whole')
        elif state=='full_text_correspondence':
            require(bool(all_paragraphs) and mapped==sorted(all_paragraphs) and r['whole_document_correspondence'] is True,'incomplete_full_text')
            require(bool(rows) and all(x.get('human_listened') is True for x in rows) and bool(r.get('full_text_verification')),'unreviewed_full_text')
        else:require(r['whole_document_correspondence'] is False,'unsupported_whole_document')
        if state in {'unmapped','same_day_candidate','missing'}:require(not rows,'candidate_has_content_mapping')
        if state=='audio_only':require(media['kind']=='audio' and r.get('video_correspondence_claimed') is False,'audio_promoted_to_video')
        if state=='missing':require(bool(r.get('missing_verification')),'absence_not_verified')
    except (KeyError,TypeError,ValueError,IndexError,AttributeError):errors.append('invalid_structure')
    return sorted(set(errors))

def validate_interview_maps(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='identity_record'").fetchone():return []
    errors=[]
    for row in db.execute("SELECT id,body FROM identity_record WHERE kind='workflow-interview-map'"):
        r=json.loads(row['body'])['record']
        errors.extend({'code':'interview_map_'+code,'id':row['id']} for code in validate_map_record(r))
        for m in r.get('mappings',[]):
            target=db.execute("SELECT body FROM identity_record WHERE id=? AND kind='workflow-evidence'",(m.get('evidence_id'),)).fetchone()
            if not target:errors.append({'code':'interview_map_broken_evidence','id':row['id']});continue
            original=json.loads(target['body'])['record'].get('original_mapping',{})
            if any(original.get(k)!=m.get(k) for k in ('mapping_id','source_id','document_id','xml_paragraphs')) or [original.get('source_start_seconds'),original.get('source_end_seconds')]!=m.get('source_range'):errors.append({'code':'interview_map_evidence_mismatch','id':row['id']})
    return errors
