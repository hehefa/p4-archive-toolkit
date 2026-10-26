"""Lossless local adapter for explicitly handed-off immutable research snapshots."""
import hashlib
import json
import re
from pathlib import Path
from .core import *
from .ingest import import_bundle, read_json_stable, permission, reliability, relation


def normalize_preview_map(rows):
    """Accept two upstream field spellings without rewriting frozen evidence."""
    if not isinstance(rows,list):raise ValueError('preview map must be a list')
    result={}
    for row in rows:
        if not isinstance(row,dict):raise ValueError('invalid preview row')
        marker=row.get('marker_id',row.get('marker'))
        seconds=row.get('actual_frame_seconds',row.get('actual_seconds'))
        if 'marker_id' in row and 'marker' in row and row['marker_id']!=row['marker']:raise ValueError('conflicting preview marker aliases')
        if 'actual_frame_seconds' in row and 'actual_seconds' in row and row['actual_frame_seconds']!=row['actual_seconds']:raise ValueError('conflicting preview time aliases')
        if not isinstance(marker,str) or not marker or marker in result:raise ValueError('invalid or duplicate preview marker')
        if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not 0<=seconds<float('inf'):raise ValueError('invalid preview frame seconds')
        if not isinstance(row.get('package_path'),str) or not row['package_path']:raise ValueError('missing preview package path')
        result[marker]={**row,'marker_id':marker,'actual_frame_seconds':seconds}
    return result

def snapshot_preview_map(payloads,graph,root):
    """Use explicit preview maps or exact graph paths inside verified package members."""
    mapped=normalize_preview_map(json.loads(payloads.get('preview_map.json',b'[]')))
    marker_ids={r['id'] for r in graph['video_markers']}
    if set(mapped)-marker_ids:raise ValueError('preview references unknown marker')
    embedded=[]
    for marker in graph['video_markers']:
        if marker['id'] in mapped or not marker.get('preview_path'):continue
        path=Path(marker['preview_path'])
        if path.is_absolute():
            try:rel=path.relative_to(root)
            except ValueError:continue  # Older snapshots retain their own hash-bound refs.
        else:rel=path
        if '..' in rel.parts or rel.is_absolute():raise ValueError('invalid embedded preview path')
        name=rel.as_posix()
        if name not in payloads or rel.suffix.lower() not in {'.png','.jpg','.jpeg'}:raise ValueError('embedded preview is not a verified image member')
        embedded.append({'marker_id':marker['id'],'package_path':name,'actual_frame_seconds':marker.get('preview_seconds')})
    mapped.update(normalize_preview_map(embedded))
    return mapped

def role_representation_links(graph):
    """Navigation between explicit representations; never infer a person's identity."""
    nodes={r['id']:r for r in graph['nodes']};edges={r['id']:r for r in graph['edges']}
    sources={r['id'] for r in graph['sources']};result={}
    for row in graph['role_records']:
        edge=edges.get(row.get('original_edge_id'));node=nodes.get(row['id']);issues=[]
        edge_ok=bool(edge and row.get('person_id') in nodes and row.get('context_id') in nodes and row.get('source') in sources and edge.get('source')==row['source'] and {edge.get('from_id'),edge.get('to_id')}=={row['person_id'],row['context_id']})
        if not edge_ok:issues.append('original_edge_missing_or_fields_disagree')
        node_ok=bool(edge_ok and node and node.get('type')=='角色记录' and all(node.get(k)==row.get(k) and row.get(k) for k in ('person_id','context_id','original_edge_id')))
        if not node:issues.append('no_same_id_graph_node')
        elif not node_ok:issues.append('same_id_graph_node_fields_disagree')
        result[row['id']]={'graph_node_id':stable_id('node','p4-research-node',row['id']) if node_ok else None,'original_claim_id':stable_id('claim','p4-research-edge',row['original_edge_id']) if edge_ok else None,'basis':'explicit_ID_endpoints_and_source_only','identity_merge':False,'issues':issues}
    return result

def import_graph(db,folder,manifest_sha,local_dir):
    root=Path(folder).resolve(); manifest=root/'MANIFEST.json'
    if file_hash(manifest)!=manifest_sha:raise ValueError('fixed snapshot manifest hash mismatch')
    manifest_data=json.loads(manifest.read_text())
    entries=manifest_data['files'] if isinstance(manifest_data,dict) else manifest_data
    payloads={}
    for r in entries:
        rel=Path(r['path'])
        if rel.is_absolute() or '..' in rel.parts:raise ValueError('invalid snapshot path')
        p=root/rel
        if not p.resolve().is_relative_to(root) or p.is_symlink():raise ValueError('snapshot escaped root')
        data=p.read_bytes()
        if len(data)!=r['bytes'] or hashlib.sha256(data).hexdigest()!=r['sha256']:raise ValueError('snapshot member mismatch: '+str(rel))
        payloads[str(rel)]=data
    if 'graph/graph.json' not in payloads:
        if 'delta_79_to_80.json' in payloads:
            state=current_graph_state(db)
            verify_delta_base(json.loads(payloads['delta_79_to_80.json']),state['version'],state['graph_sha256'])
        raise ValueError('full graph required; delta-only application is not supported')
    graph=json.loads(payloads['graph/graph.json'])
    version_match=re.match(r'^(\d+)',str(graph.get('meta',{}).get('version','0')))
    version=int(version_match.group(1)) if version_match else 0
    graph_sha=hashlib.sha256(payloads['graph/graph.json']).hexdigest()
    state=current_graph_state(db)
    if state:
        if version<state['version']:raise ValueError('graph downgrade blocked; restore a verified backup for rollback')
        if version==state['version'] and graph_sha!=state['graph_sha256']:raise ValueError('same graph version has different content; explicit new version required')
        prior=Path(local_dir)/'research_snapshots'/state['manifest_sha256']/'graph/graph.json'
        if prior.exists():
            if file_hash(prior)!=state['graph_sha256']:raise ValueError('previous fixed snapshot changed')
            oldgraph=json.loads(prior.read_text())
            for section in ('nodes','edges','sources','video_index','video_markers','role_records'):
                missing={str(x['id']) for x in oldgraph.get(section,[])}-{str(x['id']) for x in graph.get(section,[])}
                if missing:raise ValueError('full graph loses stable IDs in '+section+'; explicit reconciliation required')
    # Capture the explicitly handed-off evidence; originals are never copied.

    retained=Path(local_dir)/'research_snapshots'/manifest_sha
    for name,data in payloads.items():
        p=retained/name;p.parent.mkdir(parents=True,exist_ok=True)
        if p.exists() and p.read_bytes()!=data:raise ValueError('retained evidence changed')
        if not p.exists():p.write_bytes(data)
    (retained/'MANIFEST.json').write_bytes(manifest.read_bytes())
    ents=[];rels=[]
    preview_map=snapshot_preview_map(payloads,graph,root)
    role_links=role_representation_links(graph)
    raw_nodes={r['id']:r for r in graph['nodes']}
    entry_map={r['path']:r for r in entries}
    prov=[{'manifest_sha256':manifest_sha,'snapshot_member':'graph/graph.json','snapshot_sha256':hashlib.sha256(payloads['graph/graph.json']).hexdigest()}]
    def nid(id):return stable_id('node','p4-research-node',id)
    def sid(id):return stable_id('source','p4-research-source',id)
    def cid(id):return stable_id('claim','p4-research-edge',id)
    def mid(id):return stable_id('segment','p4-research-marker',id)
    def base(id,kind,title,raw,section):
        return {'id':id,'kind':kind,'title':title or str(raw['id']),'metadata':{'upstream_id':section+':'+str(raw['id']),'upstream_record':raw,'upstream_section':section},'provenance':prov,'permission':permission(),'reliability':reliability('mixed',['Inherited research, not independently confirmed','Machine reading and visual anchors do not imply human listening']),'epistemic':'inference'}
    types={'项目':'project','人物':'person','事件':'event','戏内角色':'role','角色记录':'role','素材版本':'document','版本':'document','文本署名':'document'}
    nodeids={r['id'] for r in graph['nodes']};sourceids={r['id'] for r in graph['sources']}
    for r in graph['nodes']:
        e=base(nid(r['id']),types.get(r['type'],'claim'),r['label'],r,'nodes');e['text']=r.get('summary','') or ''
        if e['kind']=='person':e['metadata']['identity_status']='unresolved';e['metadata']['person_card_not_confirmed_identity']=True
        if r['type']=='角色记录':
            match=role_links.get(r['id'])
            linked=bool(match and match['graph_node_id'])
            e['metadata']['representation_kind']='role_node_with_assignment_row' if linked else 'role_node_unresolved_assignment' if match else 'role_position_without_assignment_row'
            if linked:e['metadata']['role_table_entity_id']=stable_id('role','p4-role-record',r['id'])
        if e['kind']=='event':
            date=r.get('date');precision='unknown';value=None
            for pattern,fmt in [(r'\d{4}-\d{2}-\d{2}','day'),(r'\d{4}-\d{2}','month'),(r'\d{4}','year')]:
                if isinstance(date,str) and re.fullmatch(pattern,date) and r.get('date_kind') not in {'mtime','file_mtime'}:value=date;precision=fmt;break
            e['event_time']={'value':value,'precision':precision,'source':'Upstream date, date_kind retained in metadata; not independently dated'}
        ents.append(e)
    for r in graph['sources']:
        e=base(sid(r['id']),'source','研究来源 '+str(r['id']),r,'sources');e['text']=r.get('text','') or ''
        e['metadata'].update({'local_path':r.get('original_path') or r.get('path'),'research_path':r.get('path'),'recorded_sha256':r.get('sha256'),'not_independent_of_derived_research':True})
        e['epistemic']='record';ents.append(e)
    for r in graph['video_index']:
        # Reuse legacy path ID. Keep prior local metadata instead of creating a second asset universe.
        id=r.get('workbench_id') or r['id'];e=base(id,'asset',r.get('name') or id,r,'video_index')
        old=db.execute('SELECT body FROM entity WHERE id=?',(id,)).fetchone()
        if old:
            previous=json.loads(old[0]);e['metadata']={**previous['metadata'],**e['metadata']};e['provenance']=list({canonical(x):x for x in previous['provenance']+prov}.values())
            e['permission']=previous['permission']
        e['metadata'].update({'local_path':r['path'],'duration_seconds':r.get('duration_seconds'),'legacy_path_id':id,'identity_rule':'Location hash is not content hash','browser_playback':'not_retested_by_this_system'})
        e['epistemic']='record';ents.append(e)
    videoids={r['id']:(r.get('workbench_id') or r['id']) for r in graph['video_index']}
    for r in graph['edges']:
        e=base(cid(r['id']),'claim',r['verb'] or '研究关系',r,'edges');e['text']=r.get('excerpt','') or ''
        e['refs']=[nid(r['from_id']),nid(r['to_id']),sid(r['source'])];e['metadata']['atomic_claim_status']='legacy edge may contain multiple claims; not yet atomized'
        ents.append(e)
        rr=relation(nid(r['from_id']),'related_to',nid(r['to_id']));rr['id']=stable_id('rel','p4-research-edge',r['id']);rr['epistemic']='inference';rr['provenance']=prov;rr['legacy_claim_id']=e['id'];rr['verb']=r['verb'];rels.append(rr)
        rels.append(relation(e['id'],'supports',sid(r['source'])))
    for r in graph['video_markers']:
        e=base(mid(r['id']),'segment',r['title'],r,'video_markers');e['source_id']=videoids[r['video_id']]
        e.update({'start':r['start_seconds'],'end':r['end_seconds'],'time_basis':'source_zero','refs':[videoids[r['video_id']]]+[cid(x) for x in r.get('edge_ids',[])]+[nid(x) for x in r.get('event_ids',[]) if x in nodeids]})
        e['text']=canonical(r.get('process',{}));e['reliability']=reliability('mixed',['No new human listening performed','Original-source time is not YouTube time']+list(r.get('uncertainties',[])))
        if r.get('role_nodes'):
            resolved=[];unresolved=[]
            for raw_id in r['role_nodes']:
                target=raw_nodes.get(raw_id)
                if target and target.get('type') in {'人物','角色记录','戏内角色'}:resolved.append(nid(raw_id))
                elif not target and raw_id in role_links:resolved.append(stable_id('role','p4-role-record',raw_id))
                else:unresolved.append(raw_id)
            e['refs']=sorted(set(e['refs']+resolved))
            e['metadata']['explicit_role_references']={'resolved_ids':sorted(set(resolved)),'unresolved_upstream_ids':unresolved,'basis':'explicit_role_nodes_only','speaker_identity_confirmed':False}
        fallback=('HEVC' in str(r.get('location_status','')) or 'HEVC' in str(r.get('browser_decode_status','')) or bool(r.get('preview_path')))
        e['metadata']['playback_evidence_class']='static_fallback_only' if fallback else 'inherited_browser_location_record_not_retested'
        if fallback:e['metadata']['browser_playback']='Static-frame evidence only; decoded playback and listening not verified by this system'
        preview=preview_map.get(r['id'])
        if preview:
            relpath=preview['package_path']
            if relpath not in entry_map:raise ValueError('preview is not in verified manifest')
            e['metadata']['preview_ref']={'manifest_sha256':manifest_sha,'path':relpath,'sha256':entry_map[relpath]['sha256'],'actual_frame_seconds':preview['actual_frame_seconds'],'within_window':r['start_seconds']<=preview['actual_frame_seconds']<=r['end_seconds']}
        else:
            # Later packages may contain only new previews. Retain a prior hash-
            # bound frame only while this marker's source and interval are unchanged.
            for oldrow in db.execute('SELECT body FROM revision WHERE entity_id=? ORDER BY version DESC',(e['id'],)):
                previous=json.loads(oldrow[0]);oldref=previous.get('metadata',{}).get('preview_ref')
                if not oldref:continue
                if any(previous.get(k)!=e.get(k) for k in ('source_id','start','end')):break
                rel=Path(oldref['path']);preview_base=(Path(local_dir)/'research_snapshots').resolve()
                saved=preview_base/oldref['manifest_sha256']/rel
                if rel.is_absolute() or '..' in rel.parts or saved.is_symlink() or not saved.resolve().is_relative_to(preview_base) or file_hash(saved)!=oldref['sha256']:
                    raise ValueError('retained preview changed')
                e['metadata']['preview_ref']=oldref
                break
        ents.append(e)
    for r in graph['role_records']:
        e=base(stable_id('role','p4-role-record',r['id']),'role','角色记录 '+str(r['role']),r,'role_records')
        e['metadata']['assignment_status']='candidate: preserve uncertainty; not a confirmed formal responsibility'
        links=role_links[r['id']]
        e['metadata']['representation_links']=links
        e['metadata']['representation_kind']='role_assignment_with_graph_node' if links['graph_node_id'] else 'role_assignment_without_graph_node'
        e['refs']=[nid(x) for x in (r.get('person_id'),r.get('context_id')) if x in nodeids]
        if r.get('source') in sourceids:e['refs'].append(sid(r['source']))
        e['refs']+= [links[k] for k in ('graph_node_id','original_claim_id') if links[k]]
        e['text']=r.get('excerpt','') or '';ents.append(e)
    # 20 unmerged batch-96 anchors remain coverage candidates, NOT graph markers/events.
    anchors=json.loads(payloads.get('research/96/事件证据锚点.json',b'[]'))
    for r in anchors:
        e=base(stable_id('coverage','batch96-unmerged',r['id']),'coverage',r['title'],r,'batch96_unmerged')
        e['text']=r.get('claim','');e['metadata'].update({'merged_into_graph':False,'browser_playback':'HEVC unavailable in current browser; use local path, exact source time and pre-existing still frames','new_human_listening_count':0})
        e['reliability']['limitations']+=['Unmerged candidate; does not increase graph marker count','Still-frame/timecode fallback is not proof of playable or accurate dialogue'];ents.append(e)
    if version>78:
        legacy_to_marker={str(legacy):mid(r['id']) for r in graph['video_markers'] for legacy in r.get('legacy_ids',[])}
        for row in db.execute("SELECT body FROM entity WHERE kind='coverage'"):
            old=json.loads(row[0]);meta=old.get('metadata',{})
            if meta.get('upstream_section')!='batch96_unmerged':continue
            marker_id=legacy_to_marker.get(str(meta.get('upstream_record',{}).get('id')))
            if not marker_id:continue
            old['metadata']['merged_into_graph']=True;old['metadata']['merged_marker_id']=marker_id
            old['metadata']['merge_basis']='Explicit upstream marker legacy_ids; retains original candidate record'
            old['refs']=sorted(set(old.get('refs',[])+[marker_id]))
            old['provenance']=list({canonical(x):x for x in old['provenance']+prov}.values())
            ents.append(old)
    # Preserve essays/cards/revision log and other non-normalized fields losslessly in snapshot metadata.

    inventory={'id':stable_id('coverage','research-snapshot',manifest_sha),'kind':'coverage','title':'研究快照 内容'+str(version),'metadata':{'upstream_id':'snapshot:'+manifest_sha,'manifest_sha256':manifest_sha,'nodes':len(graph['nodes']),'edges':len(graph['edges']),'markers':len(graph['video_markers']),'role_records':len(graph['role_records']),'unmerged_batch96':len(anchors),'retained_files':len(entries),'public_drafts_approved':False,'content_version':version,'graph_sha256':graph_sha,'decoded_record_markers':sum(not ('HEVC' in str(r.get('location_status','')) or 'HEVC' in str(r.get('browser_decode_status','')) or bool(r.get('preview_path'))) for r in graph['video_markers']),'static_fallback_markers':sum('HEVC' in str(r.get('location_status','')) or 'HEVC' in str(r.get('browser_decode_status','')) or bool(r.get('preview_path')) for r in graph['video_markers']),'original_graph_meta':graph['meta'],'research_revision_log':graph.get('research_revision_log',[])},'permission':permission(),'reliability':reliability('document_record',['Private snapshot; 3 new drafts are not the previously approved 8 articles']),'provenance':prov}
    ents.append(inventory)
    ents.append({'id':stable_id('coverage','research-state','active'),'kind':'coverage','title':'当前研究图版本','metadata':{'upstream_id':'state:p4-research','version':version,'graph_sha256':graph_sha,'manifest_sha256':manifest_sha,'source_scope':'full graph; no delta applied'},'permission':permission(),'reliability':reliability('document_record'),'provenance':prov})
    bundle={'format':'p4-evidence-v1','entities':ents,'relations':rels}
    result=import_bundle(db,bundle,'p4-fixed-research-content78',str(root),local_dir)
    result['upstream_counts']={k:len(graph[k]) for k in ('nodes','edges','video_markers','role_records')};result['unmerged_batch96']=len(anchors);result['verified_snapshot_members']=len(entries);result['source_version']=version;result['decoded_record_markers']=inventory['metadata']['decoded_record_markers'];result['static_fallback_markers']=inventory['metadata']['static_fallback_markers']
    return result


def current_graph_state(db):
    row=db.execute('SELECT body FROM entity WHERE id=?',(stable_id('coverage','research-state','active'),)).fetchone()
    if row:
        m=json.loads(row[0])['metadata'];return {'version':m['version'],'graph_sha256':m['graph_sha256'],'manifest_sha256':m['manifest_sha256']}
    candidates=[]
    for row in db.execute("SELECT body FROM entity WHERE kind='coverage'"):
        e=json.loads(row[0]);m=e.get('metadata',{});g=m.get('original_graph_meta')
        if not g:continue
        match=re.match(r'^(\d+)',str(g.get('version','0')))
        if not match:continue
        sha=m.get('graph_sha256') or next((x.get('snapshot_sha256') for x in e.get('provenance',[]) if x.get('snapshot_member')=='graph/graph.json'),None)
        if sha:candidates.append({'version':int(match.group(1)),'graph_sha256':sha,'manifest_sha256':m['manifest_sha256']})
    return max(candidates,key=lambda x:x['version']) if candidates else None

def verify_delta_base(delta,version,sha256):
    if delta.get('schema')!='p4-keyed-graph-delta-v1':raise ValueError('unsupported delta schema')
    if version!=delta.get('required_base_version') or sha256!=delta.get('required_base_sha256'):raise ValueError('delta base version/hash mismatch; use the complete graph for this database')
    return {'base_matched':True,'required_base_version':version,'result_version':delta['result_version'],'applied':False}
