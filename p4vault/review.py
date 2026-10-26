"""Private review overlay. Separate tables, source-bound navigation, no public grants."""
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from .core import canonical,digest,file_hash,stable_id,now,audit,entities


def bounded_read(path):
    p=Path(path)
    if not p.is_absolute() or p.is_symlink() or any(x in {'.codex','.ssh','.aws','.agents'} for x in p.parts):raise ValueError('unsafe review source path')
    if p.suffix.lower() not in {'.json','.md','.txt','.csv','.docx','.html','.png','.gif'} or not p.is_file() or p.stat().st_size>16*1024*1024:raise ValueError('unsupported review source')
    return p.read_bytes()


def anchor_parts(anchor):
    parts=[(kind,int(start),int(end or start)) for kind,start,end in re.findall(r'([LP])(\d+)(?:\s*[–—-]\s*[LP]?(\d+))?',anchor)]
    if not parts or any(start<1 or end<start or end-start>300 for _,start,end in parts):raise ValueError('invalid review anchor')
    return parts


def excerpt_source(path,data,anchor):
    if Path(path).suffix.lower()=='.docx':
        import io
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if z.getinfo('word/document.xml').file_size>32*1024*1024:raise ValueError('oversized source XML')
            xml=ET.fromstring(z.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        lines=[''.join(t.text or '' for t in p.findall('.//w:t',ns)) for p in xml.findall('.//w:p',ns)]
        lines=[x for x in lines if x.strip()];prefix='P';method='nonempty Word XML paragraphs, one-based'
    else:lines=data.decode('utf-8-sig').splitlines();prefix='L';method='UTF-8 source lines, one-based'
    wanted=set()
    for kind,start,end in anchor_parts(anchor):
        if kind!=prefix or end>len(lines):raise ValueError('review anchor outside source')
        wanted.update(range(start,end+1))
    return {'anchor':anchor,'numbering':method,'lines':[{'location':prefix+str(i),'text':lines[i-1]} for i in sorted(wanted)],'source_sha256':__import__('hashlib').sha256(data).hexdigest()}


def _member_payloads(root,manifest_sha):
    root=Path(root).resolve()
    manifest=root/'MANIFEST.json'
    if file_hash(manifest)!=manifest_sha:raise ValueError('review manifest mismatch')
    spec=json.loads(manifest.read_text());rows=spec.get('files') if isinstance(spec,dict) else spec
    if rows is None:rows=[{'path':name,'sha256':sha} for name,sha in spec.items()]
    if len(rows)>1000 or len({r['path'] for r in rows})!=len(rows):raise ValueError('invalid review manifest members')
    payload={}
    for row in rows:
        rel=Path(row['path']);p=root/rel
        if rel.is_absolute() or '..' in rel.parts or p.is_symlink() or not p.resolve().is_relative_to(root) or p.stat().st_size>20*1024*1024:raise ValueError('unsafe review member')
        raw=p.read_bytes()
        if __import__('hashlib').sha256(raw).hexdigest()!=row['sha256'] or ('bytes' in row and len(raw)!=row['bytes']):raise ValueError('review member mismatch')
        payload[rel.as_posix()]=raw
    return payload,manifest.read_bytes()


def source_registry(data):
    registry={}
    def add(row):
        path=row['path'];sha=row['sha256']
        if not re.fullmatch(r'[a-f0-9]{64}',sha):raise ValueError('invalid source hash')
        if path in registry and registry[path]['sha256']!=sha:raise ValueError('conflicting review source hashes')
        registry[path]=row
    for row in data.get('sources',{}).values():add(row)
    for package in data.get('inherited_packages',{}).values():
        for key in ('sources','source_hashes'):
            rows=package.get(key,[])
            for row in rows.values() if isinstance(rows,dict) else rows:add(row)
    return registry


def _source_paths(e):
    meta=e.get('metadata',{});raw=meta.get('upstream_record',{})
    return {str(x) for x in (meta.get('local_path'),meta.get('research_path'),raw.get('path'),raw.get('original_path')) if x}


def map_record(row,base_entities,registry):
    """Explicit upstream IDs plus exact source paths; labels alone never map identity."""
    by_upstream={}
    for e in base_entities:
        raw=e.get('metadata',{}).get('upstream_record',{})
        if 'id' in raw:by_upstream[(e['metadata'].get('upstream_section'),raw['id'])]=e
    sources=[e for e in base_entities if e['kind']=='source' and row['source'] in _source_paths(e)]
    source_ids={e['id'] for e in sources};links={};unresolved=[]
    def link(e,kind,basis):links[(e['id'],kind)]={'entity_id':e['id'],'kind':kind,'basis':basis}
    for e in sources:link(e,'source','exact_source_path_and_verified_review_source_hash')
    original=row.get('original_record') or {};matching_edges=[]
    for id in original.get('base_edge_ids',[]):
        edge=by_upstream.get(('edges',id))
        if edge and set(edge.get('refs',[]))&source_ids:
            link(edge,'claim','explicit_base_edge_id_and_same_source');matching_edges.append(edge)
        else:unresolved.append({'reference':id,'reason':'base_edge_source_not_matched'})
    explicit=[]
    for key in ('from_person','to_person','person'):
        id=original.get(key)
        if not id:continue
        e=by_upstream.get(('nodes',id))
        on_base_edge=bool(e and any(e['id'] in edge.get('refs',[]) for edge in matching_edges))
        declared_source=bool(original.get('source')==row['source'] and row['source'] in registry and original.get('source_sha256',registry[row['source']]['sha256'])==registry[row['source']]['sha256'])
        if e and e['kind']=='person' and (on_base_edge or declared_source):
            link(e,'person','explicit_person_id_on_source_bound_base_edge' if on_base_edge else 'explicit_person_id_and_same_verified_review_source');explicit.append({'role':key,'source_id':id,'entity_id':e['id'],'label':e['title'],'status':'source_bound_navigation_not_identity_confirmation'})
        else:unresolved.append({'reference':id,'role':key,'reason':'identity_or_source_evidence_unresolved'})
    for edge in matching_edges:
        for id in edge.get('refs',[]):
            e=next((x for x in base_entities if x['id']==id),None)
            if e and e['kind'] in {'project','event'}:link(e,e['kind'],'context_of_explicit_source_bound_base_edge')
    for id in (original.get('event_id'),original.get('base_event')):
        if not id:continue
        e=by_upstream.get(('nodes',id))
        if e and e['kind']=='event' and any(e['id'] in edge.get('refs',[]) for edge in matching_edges):link(e,'event','explicit_event_id_on_source_bound_base_edge')
    return {'links':list(links.values()),'explicit_people':explicit,'unresolved':unresolved,'name_only_merge':False,'unmapped_people_labels_retained':row['people']}



def verify_delta(db,payload,data,member,local_dir,manifest_sha):
    delta=json.loads(payload[member])
    if not re.fullmatch(r'p4\.private\.people-delta/\d+',delta.get('schema','')) or delta.get('removed_ids') or delta.get('replaced_records'):raise ValueError('append-only review delta required')
    parent=db.execute('SELECT * FROM review_package WHERE manifest_sha256=?',(delta['parent_manifest_sha256'],)).fetchone()
    if not parent:raise ValueError('review delta parent not imported')
    receipt=json.loads(parent['body']);folder=Path(local_dir)/parent['snapshot_path']
    parent_payload,_=_member_payloads(folder,delta['parent_manifest_sha256']);raw=parent_payload[receipt['data_member']]
    if __import__('hashlib').sha256(raw).hexdigest()!=delta['parent_sha256'] or __import__('hashlib').sha256(bounded_read(delta['parent_path'])).hexdigest()!=delta['parent_sha256']:raise ValueError('review delta parent data mismatch')
    prior=json.loads(raw)
    if delta.get('from_schema',prior['schema'])!=prior['schema'] or data['base_graph_sha256']!=prior['base_graph_sha256']:raise ValueError('review delta base mismatch')
    inherited={r['id']:r for r in prior['records']};added={r['id']:r for r in delta['added_records']};actual={r['id']:r for r in data['records']}
    if set(delta['inherited_ids'])!=set(inherited) or len(delta['inherited_ids'])!=len(inherited) or len(added)!=len(delta['added_records']) or set(inherited)&set(added) or actual!={**inherited,**added}:raise ValueError('review delta does not reconstruct unchanged inherited records')
    overlays=delta.get('evidence_overlays',[])
    if data.get('evidence_overlays',[])!=prior.get('evidence_overlays',[])+overlays:raise ValueError('review overlay history mismatch')
    allowed={'append_evidence_overlay','append_comparison_overlay','append_context_overlay','append_role_evidence','append_provenance_correction'}
    for o in overlays:
        if o['id'] not in inherited or o.get('operation') not in allowed or o.get('changed_claim',False) is not False or not set(o.get('supplement_ids',[]))<=set(actual) or not o.get('note'):raise ValueError('invalid append-only review overlay')
    declared=delta.get('counts',{});counts={}
    for key,alias in [('inherited','inherited_records'),('added','added_records')]:
        if key in declared and alias in declared and declared[key]!=declared[alias]:raise ValueError('conflicting review count aliases')
        counts[key]=declared.get(key,declared.get(alias))
    counts.update(total=declared.get('total'),overlays=declared.get('overlays',0))
    if counts!={'inherited':len(inherited),'added':len(added),'overlays':len(overlays),'total':len(actual)}:raise ValueError('review delta counts mismatch')
    if len(set(actual))!=len(data['records']):raise ValueError('duplicate delta records')
    heads={}
    new_package=stable_id('review-package','p4-private-people-review',manifest_sha)
    # Reject unrelated local edits and missing baselines; replay accepts exactly this delta's result.
    for upstream,row in inherited.items():
        id=stable_id('review','p4-private-people-review',upstream);head=db.execute('SELECT * FROM review_record WHERE id=?',(id,)).fetchone()
        if not head:raise ValueError('review delta inherited head missing')
        body=json.loads(head['body'])
        expected_overlays=[o for o in prior.get('evidence_overlays',[]) if o['id']==upstream]
        next_overlays=expected_overlays+[o for o in overlays if o['id']==upstream]
        if body['record']!=row or body.get('evidence_overlays',[]) not in (expected_overlays,next_overlays):raise ValueError('review delta inherited head drift')
        history=db.execute('SELECT package_id FROM review_revision WHERE record_id=?',(id,)).fetchall()
        ancestor_ids=set(receipt.get('ancestor_package_ids',[]))|{parent['id'],new_package}
        if head['package_id'] not in ancestor_ids or not history:raise ValueError('review delta lineage mismatch')
        heads[upstream]=body
    for upstream in added:
        head=db.execute('SELECT package_id FROM review_record WHERE upstream_id=?',(upstream,)).fetchone()
        if head and head[0]!=new_package:raise ValueError('review delta added ID already exists')
    return {'delta':delta,'prior':prior,'receipt':receipt,'parent_id':parent['id'],'heads':heads}


def evidence_registry(payload,members):
    rows=[];tables={}
    for member in members:
        if member not in payload:raise ValueError('evidence ledger outside verified package')
        obj=json.loads(payload[member])
        if isinstance(obj,dict) and 'table_rows' in obj:
            rows.append({'path':obj['source'],'sha256':obj['sha256']});tables[obj['source']]=obj
        else:rows.extend(obj.values() if isinstance(obj,dict) else obj)
    return rows,tables


def structured_evidence(path,raw,anchor,registry,tables):
    suffix=Path(path).suffix.lower();sha=__import__('hashlib').sha256(raw).hexdigest()
    if suffix in {'.png','.gif'}:
        import struct
        if suffix=='.png' and raw[:8]==b'\x89PNG\r\n\x1a\n' and raw[12:16]==b'IHDR':dimensions=list(struct.unpack('>II',raw[16:24]))
        elif suffix=='.gif' and raw[:6] in (b'GIF87a',b'GIF89a'):dimensions=list(struct.unpack('<HH',raw[6:10]))
        else:raise ValueError('invalid declared image header')
        entry=registry[path]
        if entry.get('dimensions')!=dimensions or not entry.get('observation'):raise ValueError('image ledger dimensions or observation missing')
        related=[x for x in registry.values() if x.get('article') and x.get('article')==entry.get('article') and Path(x['path']).suffix.lower() in {'.png','.gif'}]
        return {'anchor':anchor,'numbering':'declared visual region; no text-line coordinates','lines':[],'source_sha256':sha,'kind':'image_document','dimensions':dimensions,'upstream_observation':entry,'related_image_ledger':related,'current_import_visual_review':False,'human_listening_added':False}
    if suffix=='.html':
        from html.parser import HTMLParser
        class Tables(HTMLParser):
            def __init__(self):super().__init__();self.tables=[];self.stack=[]
            def handle_starttag(self,tag,attrs):
                if tag=='table':self.stack.append({'rows':[],'cell':None})
                elif self.stack and tag=='tr':self.stack[-1]['rows'].append([])
                elif self.stack and tag in {'td','th'}:
                    a=dict(attrs)
                    if not self.stack[-1]['rows']:self.stack[-1]['rows'].append([])
                    self.stack[-1]['cell']={'parts':[],'rowspan':a.get('rowspan','1'),'colspan':a.get('colspan','1')}
            def handle_data(self,text):
                if self.stack and self.stack[-1]['cell'] is not None:self.stack[-1]['cell']['parts'].append(text)
            def handle_endtag(self,tag):
                if self.stack and tag in {'td','th'} and self.stack[-1]['cell'] is not None:
                    c=self.stack[-1]['cell'];c['text']=' '.join(' '.join(c.pop('parts')).split());self.stack[-1]['rows'][-1].append(c);self.stack[-1]['cell']=None
                elif self.stack and tag=='table':self.tables.append(self.stack.pop()['rows'])
        descriptor=tables.get(path)
        if not descriptor or descriptor['sha256']!=sha:raise ValueError('HTML structure evidence required')
        parser=Tables();parser.feed(raw.decode('utf-8-sig'));expected=descriptor['table_rows'][:2]
        def same(rows):return len(rows)>=2 and all(len(rows[i])==len(r['cells']) and all(c['text']==' '.join(e['text'].split()) and c['rowspan']==str(e.get('rowspan','1'))=='1' and c['colspan']==str(e.get('colspan','1'))=='1' for c,e in zip(rows[i],r['cells'])) for i,r in enumerate(expected))
        found=[(i,rows) for i,rows in enumerate(parser.tables,1) if same(rows)]
        if not found:raise ValueError('HTML source table differs from declared structure')
        index,rows=found[0]
        return {'anchor':anchor,'numbering':'verified HTML table row/column; one-based','source_sha256':sha,'kind':'html_table','lines':[{'location':f'T{index}R{i}C{j}','text':c['text']} for i,row in enumerate(rows[:2],1) for j,c in enumerate(row,1)],'upstream_method':descriptor.get('method',''),'table_matches':len(found)}
    return excerpt_source(path,raw,anchor)


def import_review(db,folder,manifest_sha,local_dir,data_member='统一人员事件审阅.json',delta_member=None,evidence_members=()):
    from .graph_adapter import current_graph_state
    root=Path(folder).resolve();payload,manifest_raw=_member_payloads(root,manifest_sha)
    if data_member not in payload:raise ValueError('review data not in manifest')
    data=json.loads(payload[data_member]);state=current_graph_state(db)
    if not re.fullmatch(r'p4\.private\.people-review/\d+',data.get('schema','')) or data.get('review_only') is not True or data.get('visibility') not in {'local_private','private_local'}:raise ValueError('private review schema required')
    if not state or state['graph_sha256']!=data.get('base_graph_sha256'):raise ValueError('review base graph mismatch')
    context=verify_delta(db,payload,data,delta_member,local_dir,manifest_sha) if delta_member else None
    if data.get('evidence_overlays') and not context:raise ValueError('review overlays require explicit delta import')
    extra,tables=evidence_registry(payload,evidence_members)
    predecessor_receipts=[]
    dependencies=list(data.get('predecessors',[]))
    if '前置包及修订记录.json' in payload:dependencies+=json.loads(payload['前置包及修订记录.json']).get('manifests',[])
    for item in dependencies:
        raw=bounded_read(item['path'])
        if __import__('hashlib').sha256(raw).hexdigest()!=item['sha256']:raise ValueError('review predecessor mismatch')
        predecessor_receipts.append(item)
    rows=data.get('records',[])
    if not rows or len({r['id'] for r in rows})!=len(rows):raise ValueError('duplicate or empty review records')
    registry=source_registry(data);source_bytes={}
    if context:
        for path,sha in context['receipt']['source_hashes'].items():
            if path in registry and registry[path]['sha256']!=sha:raise ValueError('inherited source hash drift')
            registry.setdefault(path,{'path':path,'sha256':sha})
    for entry in extra:
        path=entry['path']
        if path in registry and registry[path]['sha256']!=entry['sha256']:raise ValueError('evidence source hash conflict')
        registry[path]={**registry.get(path,{}),**entry}
    for path,entry in registry.items():
        raw=bounded_read(path)
        if __import__('hashlib').sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('review source hash mismatch')
        source_bytes[path]=raw
    for overlay in data.get('evidence_overlays',[]):
        if overlay.get('prior_source') and overlay['prior_source'] not in source_bytes:raise ValueError('provenance correction source not verified')
    current=entities(db);prepared=[]
    for row in rows:
        for key in ('id','project','event','relation','strength','claim','source','anchor','limit'):
            if not isinstance(row.get(key),str) or not row[key]:raise ValueError('missing review record field')
        if not isinstance(row.get('people'),list) or not all(isinstance(x,str) for x in row['people']):raise ValueError('invalid people labels')
        if row['source'] not in source_bytes:raise ValueError('review source has no verified hash')
        inherited_body=context['heads'].get(row['id']) if context else None
        evidence=inherited_body['source_evidence'] if inherited_body else structured_evidence(row['source'],source_bytes[row['source']],row['anchor'],registry,tables)
        mapping=map_record(row,current,registry)
        original=row.get('original_record') or {}
        event_title=row['event']
        for inherited in data.get('inherited_packages',{}).values():
            for stage in inherited.get('stages',[])+inherited.get('events',[]):
                if stage.get('id')==row['event']:event_title=stage.get('title',row['event'])
        stage=next((x for x in data.get('project_stages',[]) if row['id'] in x.get('records',[])),None)
        if stage and stage.get('id')==row['event']:event_title=stage.get('title',event_title)
        body={'record':row,'event_title':event_title,'layer_schema':data['schema'],'review_only':True,'visibility':'local_private','event_is_base_graph_fact':False,'identity_policy':data.get('identity_policy',''),'event_model':data.get('event_model',''),'mapping':mapping,'source_evidence':evidence,'direct_dialogue_record':original.get('direct_dialogue_record',row.get('direct_dialogue_record')),'direction':original.get('direction') or row.get('direction') or ('explicit_from_to' if original.get('from_person') and original.get('to_person') else 'not_structured'),'source_nature':row['strength']}
        if stage:body['event_stage']=stage
        comparisons=[x for x in data.get('research_comparisons',[]) if row['id'] in x.get('records',[])]
        if comparisons:body['research_comparisons']=comparisons
        if 'both_sides_independently_confirmed' in row:body['both_sides_independently_confirmed']=row['both_sides_independently_confirmed']
        source_people={k:row[k] for k in ('from_person','to_person') if isinstance(row.get(k),str) and row[k]}
        if source_people:body['source_direction_labels']=source_people
        if inherited_body:body=json.loads(canonical(inherited_body))
        overlays=[o for o in data.get('evidence_overlays',[]) if o['id']==row['id']]
        if overlays:body['evidence_overlays']=overlays
        id=stable_id('review','p4-private-people-review',row['id']);prepared.append((id,row['id'],body))
    package_id=stable_id('review-package','p4-private-people-review',manifest_sha)
    package={'schema':data['schema'],'review_only':True,'base_graph_sha256':data['base_graph_sha256'],'manifest_sha256':manifest_sha,'data_member':data_member,'record_ids':[x[0] for x in prepared],'predecessors':predecessor_receipts,'source_hashes':{k:v['sha256'] for k,v in registry.items()}}
    if context:
        package['delta_member']=delta_member;package['declared_counts']=context['delta']['counts'];package['ancestor_package_ids']=context['receipt'].get('ancestor_package_ids',[])+[context['parent_id']]
        package['evidence_members']=list(evidence_members)
    existing=db.execute('SELECT id FROM review_package WHERE manifest_sha256=?',(manifest_sha,)).fetchone()
    if existing:
        for id,_,_ in prepared:
            head=db.execute('SELECT package_id FROM review_record WHERE id=?',(id,)).fetchone()
            if head and head[0]!=existing[0]:
                later=db.execute('SELECT body FROM review_package WHERE id=?',(head[0],)).fetchone()
                if later and existing[0] in json.loads(later[0]).get('ancestor_package_ids',[]):raise ValueError('refuse stale review package replay')
        unchanged=all((head:=db.execute('SELECT digest,package_id FROM review_record WHERE id=?',(id,)).fetchone()) and head['digest']==digest(body) for id,_,body in prepared)
        if unchanged:return {'status':'already_imported','package_id':existing[0],'records':len(rows),'base_graph_changed':False}
    retained=Path(local_dir)/'research_snapshots'/('review-'+manifest_sha);retained.mkdir(parents=True,exist_ok=True)
    for name,raw in {**payload,'MANIFEST.json':manifest_raw}.items():
        path=retained/name;path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and path.read_bytes()!=raw:raise ValueError('retained review evidence changed')
        path.write_bytes(raw)
    changed=0
    with db:
        db.execute('INSERT OR IGNORE INTO review_package VALUES(?,?,?,?,?,?)',(package_id,manifest_sha,canonical(package),digest(package),str(retained.relative_to(Path(local_dir))),now()))
        for id,upstream,body in prepared:
            old=db.execute('SELECT * FROM review_record WHERE id=?',(id,)).fetchone();sha=digest(body)
            if old and old['digest']==sha:continue
            version=old['version']+1 if old else 1;at=now()
            db.execute('INSERT INTO review_record VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET package_id=excluded.package_id,body=excluded.body,digest=excluded.digest,version=excluded.version,updated_at=excluded.updated_at',(id,upstream,package_id,canonical(body),sha,version,at))
            db.execute('INSERT INTO review_revision VALUES(?,?,?,?,?,?)',(id,version,package_id,canonical(body),sha,at));db.execute('DELETE FROM review_entity_link WHERE review_id=?',(id,))
            for item in body['mapping']['links']:db.execute('INSERT INTO review_entity_link VALUES(?,?,?,?)',(id,item['entity_id'],item['kind'],item['basis']))
            changed+=1
        audit(db,'import_private_review',{'package_id':package_id,'manifest_sha256':manifest_sha,'records':len(rows),'base_graph_changed':False})
    return {'status':'imported','package_id':package_id,'records':len(rows),'revisions_written':changed,'verified_members':len(payload),'verified_source_files':len(source_bytes),'verified_predecessors':len(dependencies),'base_graph_changed':False,'public_grants_added':0}


def available(db):return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_record'").fetchone())


def review_search(db,query='',person='',project='',relation='',nature='',entity=''):
    if not available(db):return {'records':[],'count':0,'review_only':True}
    linked={r[0] for r in db.execute('SELECT review_id FROM review_entity_link WHERE entity_id=?',(entity,))} if entity else None
    found=[]
    for row in db.execute('SELECT id,body,version FROM review_record ORDER BY upstream_id'):
        body=json.loads(row['body']);r=body['record']
        if linked is not None and row['id'] not in linked:continue
        if person and person not in r['people'] or project and project!=r['project'] or relation and relation!=r['relation'] or nature and nature!=r['strength']:continue
        if query and query.casefold() not in canonical([r,body.get('archive_context',{}).get('scoped_aliases',[])]).casefold():continue
        found.append({'id':row['id'],'upstream_id':r['id'],'people':r['people'],'project':r['project'],'event':body.get('event_title',r['event']),'relation':r['relation'],'source_nature':r['strength'],'event_date':r.get('event_date'),'event_time_note':r.get('event_time_note',''),'claim':r['claim'],'version':row['version']})
    return {'records':found,'count':len(found),'review_only':True,'counts_as_base_graph_facts':False}


def review_facets(db):
    rows=review_search(db)['records']
    return {'person':sorted({x for r in rows for x in r['people']}),'project':sorted({r['project'] for r in rows}),'relation':sorted({r['relation'] for r in rows}),'nature':sorted({r['source_nature'] for r in rows})}


def review_detail(db,id):
    row=db.execute('SELECT * FROM review_record WHERE id=?',(id,)).fetchone()
    if not row:raise ValueError('private review record not found')
    body=json.loads(row['body']);links=[]
    for link in body['mapping']['links']:
        e=db.execute('SELECT kind,title FROM entity WHERE id=?',(link['entity_id'],)).fetchone()
        if e:links.append({**link,'kind':e['kind'],'title':e['title']})
    overlays=[]
    for item in db.execute("SELECT id,kind,body FROM identity_record WHERE kind IN ('process-version','archive-amendment')"):
        b=json.loads(item['body'])
        if any(x['target']=='review' and x['id']==id for x in b['links']):overlays.append({'id':item['id'],'kind':item['kind'],'label':b['record'].get('status') or b['record'].get('scope') or item['kind']})
    return {'id':id,'version':row['version'],'body':body,'entity_links':links,'private_overlays':overlays,'history':[dict(x) for x in db.execute('SELECT version,digest,changed_at FROM review_revision WHERE record_id=? ORDER BY version DESC',(id,))]}


def validate_review_layer(db):
    if not available(db):return []
    errors=[]
    for p in db.execute('SELECT * FROM review_package'):
        data=json.loads(p['body'])
        if digest(data)!=p['digest'] or data.get('review_only') is not True:errors.append({'code':'private_review_package_digest','id':p['id']})
    for r in db.execute('SELECT * FROM review_record'):
        data=json.loads(r['body']);history=db.execute('SELECT version,digest,body,package_id FROM review_revision WHERE record_id=? ORDER BY version DESC',(r['id'],)).fetchall()
        if digest(data)!=r['digest'] or data.get('review_only') is not True or data.get('visibility')!='local_private':errors.append({'code':'private_review_digest','id':r['id']})
        if not history or tuple(history[0])!=(r['version'],r['digest'],r['body'],r['package_id']) or len(history)!=r['version']:errors.append({'code':'private_review_history','id':r['id']})
        if data.get('archive_context'):
            from .archive import validate_archive_context
            errors.extend(validate_archive_context(db,data))
        if data.get('video_context'):
            from .video import validate_video_context
            errors.extend(validate_video_context(db,data))
        expected={(x['entity_id'],x['kind'],x['basis']) for x in data['mapping']['links']}
        actual={tuple(x) for x in db.execute('SELECT entity_id,kind,basis FROM review_entity_link WHERE review_id=?',(r['id'],))}
        if actual!=expected:errors.append({'code':'private_review_link_digest','id':r['id']})
    for r in db.execute('SELECT record_id,body,digest FROM review_revision'):
        if digest(json.loads(r['body']))!=r['digest']:errors.append({'code':'private_review_revision_digest','id':r['record_id']})
    return errors
