"""Version and paragraph checks for explicitly approved public method editions."""
import hashlib,re
from urllib.parse import urlsplit,unquote
from .core import digest
from .publish import scan_sensitive


def paragraphs(text,language):
    return [{'id':language+'-P'+str(n).zfill(3),'text_sha256':hashlib.sha256(t.encode()).hexdigest(),'heading':t.startswith('## '),'labels':sorted(set(re.findall(r'\[([SR]\d+)\]',t)))} for n,t in enumerate(text.split('\n\n'),1)]


def validate_payload(p):
    if p.get('schema')!='p4_public_method_essay_v1' or p.get('status')!='production_verified' or scan_sensitive(p):raise ValueError('invalid public method payload')
    if p.get('new_articles')!=1 or any(p.get(k)!=0 for k in ('new_event_reconstructions','new_videos','new_images','new_projects')):raise ValueError('method scope must exclude event and media publication')
    a=p['article'];sources=p['sources'];refs=a['references']+a['readingReferences'];labels=[r['label'] for r in refs]
    if set(p)-{'schema','status','commit','deployment','article','sources','urls','scope','new_articles','new_public_sources','new_event_reconstructions','new_videos','new_images','new_projects'}:raise ValueError('unexpected public payload field')
    if set(a)-{'id','kind','methodVersion','date','dateLabel','savedAt','dependencyState','epistemic_status','epistemic_status_en','references','readingReferences','relatedProjects','revisionNote','revisionNote_en','title','title_en','summary','summary_en','body','body_en','articleVersion'}:raise ValueError('unexpected public article field')
    source_fields={'id','title','type','project_slug','summary','body','boundary','status','date','catalogueDate','dateLabel','url','sourceSha256','importScope','facts','snapshotVersion'}
    if any(set(s)-source_fields for s in sources):raise ValueError('unexpected public source field')
    if a['kind']!='public_method' or not re.fullmatch('[a-z0-9-]+',a['id']) or a['articleVersion']!=digest({k:v for k,v in a.items() if k!='articleVersion'}):raise ValueError('public method edition hash mismatch')
    if not re.fullmatch('[a-f0-9]{40}',p['commit']) or not p['deployment']:raise ValueError('publication commit or deployment missing')
    if len(set(labels))!=len(labels) or len({s['id'] for s in sources})!=len(sources) or len({unquote(s['url']) for s in sources})!=len(sources) or p['new_public_sources']!=len(sources):raise ValueError('duplicate source or citation')
    for lang in ('zh','en'):
        body=a['body_en' if lang=='en' else 'body']
        if not body.strip() or not a['title_en' if lang=='en' else 'title'] or not {x for r in paragraphs(body,lang) for x in r['labels']}<=set(labels):raise ValueError('unresolved paragraph citation')
        current='https://b.p4theater.cn'+('/en' if lang=='en' else '')+'/research/'+a['id']
        if p['urls'][lang]!=current or p['urls']['fixed_'+lang]!=current+'/versions/'+a['articleVersion']:raise ValueError('method fixed URL does not bind edition')
    for s in sources:
        u=urlsplit(s['url'])
        if u.scheme!='https' or u.netloc!='p4theater.cn' or s['snapshotVersion']!=digest({k:v for k,v in s.items() if k!='snapshotVersion'}) or not s['boundary'] or s['project_slug'] is not None:raise ValueError('source excerpt edition invalid')
    byid={s['id']:s for s in sources}
    for r in a['references']:
        s=byid.get(r['sourceId'])
        if not s or r['sourceVersion']!=s['snapshotVersion'] or r['url']!=s['url']:raise ValueError('source reference revision mismatch')
    for r in a['readingReferences']:
        if r['relation']!='method_comparison_not_causal' or r['url']!='/research/event-plans/'+r['studyId'].lower():raise ValueError('comparison cannot imply causality')
    return a


def projection_digest(documents,index,ids):
    return digest({'documents':sorted((e for e in documents['entities'] if e['id'] in ids),key=lambda e:e['id']),'sources':sorted((e for e in index['records'] if e['id'] in ids),key=lambda e:e['id'])})


def check_method_indexes(documents,index,scope):
    errors=[];docs={e['id']:e for e in documents['entities']};sources={e['id']:e for e in index['records']}
    approvals=scope.get('approved_public_methods',[])
    if len({a['article_id'] for a in approvals})!=len(approvals):errors.append('duplicate_public_method')
    authorized=set()
    for approval in approvals:
        try:
            ids=approval['document_ids']+approval['source_document_ids'];authorized.update(ids)
            if len(ids)!=len(set(ids)) or approval['destination']!='github:'+scope['repository'] or approval['permission_basis']!='explicit_user_approved' or approval['projection_sha256']!=projection_digest(documents,index,ids):raise ValueError()
            languages=[]
            for id in approval['document_ids']:
                e=docs[id];m=sources[id]['public_method'];languages.append(m['language'])
                if m['article_id']!=approval['article_id'] or m['article_version']!=approval['article_version'] or m['fixed_url']!=e['public_url']+'/versions/'+m['article_version'] or m['paragraphs']!=paragraphs(e['text'],m['language']):raise ValueError()
                refs=m['references'];labels=[r['label'] for r in refs]
                if len(labels)!=len(set(labels)) or not {x for p in m['paragraphs'] for x in p['labels']}<=set(labels):raise ValueError()
                for r in refs:
                    if r['document_id'] not in e['refs'] or r['document_id'] not in docs:raise ValueError()
                    if r['kind']=='source':
                        source=sources[r['document_id']]['public_method_source']
                        if source['snapshot_version']!=r['version'] or source['original_url']!=r['original_url'] or r['document_id'] not in approval['source_document_ids']:raise ValueError()
                    elif r['kind']!='comparison' or r['relation']!='method_comparison_not_causal':raise ValueError()
            if sorted(languages)!=['en','zh']:raise ValueError()
            originals=[unquote(sources[id]['public_method_source']['original_url']) for id in approval['source_document_ids']]
            if len(originals)!=len(set(originals)):raise ValueError()
        except (KeyError,ValueError,TypeError):errors.append('public_method_revision_or_reference')
    marked={r['id'] for r in index['records'] if 'public_method' in r or 'public_method_source' in r}
    if marked!=authorized:errors.append('public_method_unapproved_index')
    return sorted(set(errors))
