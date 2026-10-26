"""Offline checks for tracked repository files and approved public data.

Never scans local research, fetches sources, sends data, or prints matched values.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
FORBIDDEN_PARTS={'local','exports','backups','.aws','.ssh','.codex','.agents'}
BINARY_SUFFIXES={'.db','.sqlite','.sqlite3','.zip','.mp4','.mov','.m4a','.wav','.mp3','.jpg','.jpeg','.png','.heic','.pdf'}
CREDENTIAL=re.compile(r'(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
LOCAL_PATH=re.compile(r'/(?:Users|Volumes|home)/[A-Za-z0-9_. -]+/[A-Za-z0-9_. -]+')
EMAIL=re.compile(r'[\w.+-]+@([\w.-]+\.[A-Za-z]{2,})')
PHONE=re.compile(r'(?<![A-Za-z0-9])1[3-9]\d{9}(?![A-Za-z0-9])')

def privacy_codes(name,raw):
    """Returns reason codes, not contact values or potentially sensitive paths."""
    rel=Path(name);codes=[]
    if rel.is_absolute() or '..' in rel.parts or any(x in FORBIDDEN_PARTS for x in rel.parts) or rel.suffix.lower() in BINARY_SUFFIXES or any(x.startswith('.env') for x in rel.parts):
        codes.append('forbidden_tracked_path_or_binary')
    try:text=raw.decode('utf-8')
    except UnicodeDecodeError:return codes+['non_text_tracked_payload']
    if CREDENTIAL.search(text):codes.append('credential_signature')
    if LOCAL_PATH.search(text):codes.append('private_absolute_path')
    synthetic=bool(rel.parts and rel.parts[0] in {'tests','fixtures'})
    if any(not (synthetic and m.group(1).endswith('.test')) for m in EMAIL.finditer(text)):codes.append('contact_email')
    if any(not (synthetic and m.group()==('138'+'00000000')) for m in PHONE.finditer(text)):codes.append('contact_phone')
    if re.search(r'\d+\s*号楼\s*\d+',text):codes.append('detailed_address')
    return codes

def scan_tracked(root=ROOT):
    raw=subprocess.check_output(['git','-C',str(root),'ls-files','-z']);names=raw.decode().rstrip('\0').split('\0') if raw else []
    findings=[]
    for name in names:
        p=root/name;codes=[]
        if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()):codes=['tracked_symlink_or_escape']
        elif not p.is_file():codes=['tracked_file_missing']
        else:codes=privacy_codes(name,p.read_bytes())
        if codes:findings.append({'file_key':hashlib.sha256(name.encode()).hexdigest()[:12],'codes':codes})
    return {'ok':not findings,'tracked_files':len(names),'findings':findings}

def reviewed_media_visibility(record,scope):
    """Offline evidence/approval consistency, not a fresh platform visibility check."""
    if record.get('current_visibility')=='unknown':return True
    if record.get('current_visibility')!='public':return False
    evidence=record.get('verification',{});permission=record.get('permission',{})
    destination='github:'+scope.get('repository','')
    digest=evidence.get('official_readback_sha256','')
    approvals=[a for a in scope.get('approved_public_media',[]) if a.get('video_id')==record.get('video_id')]
    if len(approvals)!=1 or not re.fullmatch(r'[a-f0-9]{64}',digest):return False
    approval=approvals[0]
    if approval!={'video_id':record.get('video_id'),'destination':destination,'permission_basis':'explicit_user_approved','official_readback_sha256':digest}:return False
    if permission.get('state')!='explicit_user_approved' or permission.get('destination')!=destination or permission.get('scope')!='public_media_metadata_and_access_link' or permission.get('original_media_upload_to_repository') is not False:return False
    if evidence.get('method')!='youtube_data_api_official_readback' or evidence.get('video_id')!=record.get('video_id'):return False
    if (evidence.get('privacy_status'),evidence.get('upload_status'),evidence.get('processing_status'))!=('public','processed','succeeded'):return False
    # Public visibility and embedding permission are separate platform states.
    if type(evidence.get('embeddable')) is not bool or record.get('embeddable') is not evidence['embeddable'] or evidence.get('independent_playback_verified') is not False:return False
    if evidence['embeddable'] is False and record.get('embed_url'):return False
    if 'region_restriction' in evidence or 'region_restriction' in record:
        restriction=evidence.get('region_restriction')
        if not isinstance(restriction,dict) or record.get('region_restriction')!=restriction:return False
        if not set(restriction)<={'allowed','blocked'} or len(restriction)>1:return False
        for countries in restriction.values():
            if not isinstance(countries,list) or any(not isinstance(c,str) or not re.fullmatch(r'[A-Z]{2}',c) for c in countries) or len(countries)!=len(set(countries)):return False
    if not record.get('source_document_ids'):return False
    try:
        from datetime import datetime
        observed=datetime.fromisoformat(evidence.get('observed_at',''))
        if observed.tzinfo is None:return False
    except (ValueError,TypeError):return False
    return True


def still_payload_digest(payload):
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def reviewed_public_stills(record,scope,document_urls):
    """Verify approved still metadata within one existing public video edition; offline only."""
    payload=record.get('published_stills')
    if payload is None:return True
    try:
        allowed={'schema','status','commit','deployment','video_id','edition_title_zh','edition_title_en','urls','new_uploads','new_stills','source_basis','source_time_precision_note','frames'}
        if set(payload)!=allowed or payload['schema']!='p4_public_film_stills_v1' or payload['status']!='production_verified':return False
        if record.get('current_visibility')!='public' or not reviewed_media_visibility(record,scope):return False
        if payload['video_id']!=record['video_id'] or payload['edition_title_en']!=record.get('title') or not record.get('version'):return False
        if payload['new_uploads']!=0 or payload['source_basis']!='exact_uploaded_file_zero_offset' or not payload['source_time_precision_note']:return False
        if not re.fullmatch(r'[a-f0-9]{40}',payload['commit']) or not re.fullmatch(r'[A-Za-z0-9_-]+',payload['deployment']):return False
        approvals=[a for a in scope.get('approved_public_still_sets',[]) if a.get('video_id')==record['video_id']]
        expected={'video_id':record['video_id'],'destination':'github:'+scope['repository'],'permission_basis':'explicit_user_approved','payload_digest':still_payload_digest(payload),'scope':'published_still_metadata_and_public_links','image_binaries_in_repository':False}
        if approvals!=[expected]:return False
        urls=payload['urls']
        if set(urls)!={'zh','en','project','research'} or not set(urls.values())<=set(document_urls):return False
        if any(urlsplit(u).scheme!='https' or urlsplit(u).hostname!='b.p4theater.cn' or urlsplit(u).query or urlsplit(u).fragment for u in urls.values()):return False
        frames=payload['frames'];ids=[f['id'] for f in frames]
        if not frames or len(ids)!=len(set(ids)) or len(ids)!=payload['new_stills'] or len({f['image_url'] for f in frames})!=len(ids) or len({f['time_seconds'] for f in frames})!=len(ids):return False
        duration=re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?',record.get('platform_duration',''))
        if not duration:return False
        seconds=sum(float(x or 0)*scale for x,scale in zip(duration.groups(),[3600,60,1]))
        fields={'id','url','sha256','width','height','time_seconds','watch_url','source_basis','title_zh','title_en','caption_zh','caption_en','alt_zh','alt_en','image_url','page_zh','page_en','project_url','research_url'}
        for f in frames:
            if set(f)!=fields or not re.fullmatch(r'[a-z][a-z0-9-]{1,80}',f['id']):return False
            if not re.fullmatch(r'[a-f0-9]{64}',f['sha256']) or any(type(f[k]) is not int or not 0<f[k]<=8192 for k in ('width','height')):return False
            if type(f['time_seconds']) is not int or not 0<=f['time_seconds']<seconds:return False
            if not re.fullmatch(r'/assets/viewing-stills/[a-z0-9-]+\.webp',f['url']) or f['image_url']!='https://b.p4theater.cn'+f['url']:return False
            watch=urlsplit(f['watch_url'])
            if watch.scheme!='https' or watch.netloc!='www.youtube.com' or watch.path!='/watch' or watch.fragment or parse_qs(watch.query)!={'v':[record['video_id']],'t':[str(f['time_seconds'])+'s']}:return False
            if f['source_basis']!=payload['source_basis'] or f['project_url']!=urls['project'] or f['research_url']!=urls['research']:return False
            for lang in ('zh','en'):
                if f['page_'+lang]!=urls[lang]+'#'+f['id'] or any(not isinstance(f[k+'_'+lang],str) or not f[k+'_'+lang].strip() for k in ('title','caption','alt')):return False
        return True
    except (KeyError,ValueError,TypeError,AttributeError):return False


def check_shapes(documents,sources,media,scope,versions):
    errors=[]
    def require(condition,code):
        if not condition:errors.append(code)
    require(documents.get('format')=='p4-evidence-v1','bundle_format')
    rows=documents.get('entities',[]);ids=[e['id'] for e in rows];idset=set(ids)
    require(len(ids)==len(idset),'duplicate_document_id')
    urls=[e.get('public_url') for e in rows]
    require(len(urls)==len(set(urls)),'duplicate_document_url')
    allowed={'id','kind','title','text','public_url','refs','epistemic','reliability'}
    for e in rows:
        require(set(e)<=allowed and e.get('kind')=='document','unexpected_document_fields')
        require(bool(e.get('title')) and isinstance(e.get('text'),str),'document_text')
        u=urlsplit(e.get('public_url',''));require(u.scheme=='https' and u.hostname=='b.p4theater.cn' and not u.username and not u.password,'document_origin')
        require(set(e.get('refs',[]))<=idset,'dangling_document_reference')
    index=sources.get('records',[]);require(len(index)==len(ids) and {r['id'] for r in index}==idset,'source_index_coverage')
    byid={e['id']:e for e in rows}
    for r in index:
        require(r['id'] in byid and byid[r['id']]['public_url']==r.get('url'),'source_url_mismatch')
        require(bool(re.fullmatch(r'[a-f0-9]{64}',r.get('response_sha256') or '')),'source_hash')
    records=media.get('records',[]);vids=[r['video_id'] for r in records]
    require(len(vids)==len(set(vids))==media.get('count'),'media_count_or_duplicate')
    for r in records:
        require(bool(re.fullmatch(r'[A-Za-z0-9_-]{11}',r['video_id'])),'media_id')
        u=urlsplit(r.get('public_url',''));require(u.scheme=='https' and u.hostname=='www.youtube.com' and u.path=='/watch' and parse_qs(u.query).get('v')==[r['video_id']],'media_url')
        require(reviewed_media_visibility(r,scope) and r.get('playback_verified') is False and r.get('original_media_in_repository') is False,'media_status_requires_separate_evidence_review')
        require(set(r.get('source_document_ids',[]))<=idset,'dangling_media_source')
        require(reviewed_public_stills(r,scope,urls),'invalid_public_still_metadata_or_permission')
    approved=scope.get('approved_public_media',[])
    require(len({a.get('video_id') for a in approved})==len(approved),'duplicate_media_approval')
    require({a.get('video_id') for a in approved}<={r['video_id'] for r in records if r.get('current_visibility')=='public'},'dangling_media_approval')
    still_sets=scope.get('approved_public_still_sets',[])
    require(len({a.get('video_id') for a in still_sets})==len(still_sets),'duplicate_still_approval')
    require({a.get('video_id') for a in still_sets}=={r['video_id'] for r in records if r.get('published_stills')},'dangling_still_approval')
    require(scope.get('published_still_reference_records',0)==sum(len(r.get('published_stills',{}).get('frames',[])) for r in records),'still_count')
    if 'media_visibility_counts' in scope:
        from collections import Counter
        require(scope['media_visibility_counts']==dict(Counter(r.get('current_visibility') for r in records)),'media_visibility_count')
    require(scope.get('included_document_records')==len(rows) and scope.get('media_reference_records')==len(records),'scope_count')
    require(scope.get('visibility')=='private' and scope.get('public_site_republication_granted') is False and scope.get('raw_archive_backup_claim') is False,'scope_boundary')
    for r in versions.get('relations',[]):
        require(r.get('left_id') in idset and r.get('right_id') in idset,'dangling_version_candidate')
        require(r.get('automatic_identity_merge') is False and r.get('counts_as_independent_research') is False,'version_candidate_overclaim')
    from p4vault.public_method import check_method_indexes
    errors.extend(check_method_indexes(documents,sources,scope))
    return sorted(set(errors))

def validate_public(root=ROOT):
    from p4vault.core import connect,validate
    from p4vault.ingest import import_bundle
    from p4vault.publish import public_projection
    load=lambda name:json.loads((root/'public'/name).read_text())
    docs=load('documents.json');index=load('source-index.json');media=load('media-index.json')
    errors=check_shapes(docs,index,media,load('scope.json'),load('version-candidates.json'))
    if errors:return {'ok':False,'error_codes':errors}
    with tempfile.TemporaryDirectory(prefix='p4-public-ci-') as folder:
        local=Path(folder);db=connect(local/'vault.sqlite3')
        try:
            import_bundle(db,docs,'ci-public','approved tracked public payload',local)
            replay=import_bundle(db,docs,'ci-public','approved tracked public payload',local)
            validation=validate(db)
            if not validation['ok']:errors.append('database_integrity')
            if replay['status']!='already_imported':errors.append('import_not_idempotent')
            if public_projection(db,'public-site')['count']!=0:errors.append('unexpected_site_grants')
        finally:db.close()
    return {'ok':not errors,'error_codes':errors,'documents':len(docs['entities']),'media_references':len(media['records']),'still_references':sum(len(r.get('published_stills',{}).get('frames',[])) for r in media['records']),'underlying_media_read':False,'network_used':False}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--privacy-only',action='store_true');args=parser.parse_args()
    try:
        result={'privacy':scan_tracked()}
        if result['privacy']['ok'] and not args.privacy_only:result['public_data']=validate_public()
        ok=all(v['ok'] for v in result.values());print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if ok else 1
    except Exception as ex:
        # An exception may embed input contents: emit only its class, never payloads.
        print(json.dumps({'ok':False,'error_code':'repository_check_failed','exception_type':type(ex).__name__}));return 1
if __name__=='__main__':sys.exit(main())
