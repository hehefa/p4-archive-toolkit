import argparse
import json
import sys
from pathlib import Path
from .core import *
from .ingest import import_json,import_catalog,import_fixed25
from .publish import grant,write_public,revoke
from .backup import backup,restore

def parser():
    p=argparse.ArgumentParser(description='P4 local evidence version system; no remote/network write commands')
    p.add_argument('--db',default='local/vault.sqlite3');p.add_argument('--local',default='local')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('init')
    q=sub.add_parser('import');q.add_argument('input');q.add_argument('--namespace',required=True)
    q=sub.add_parser('import-catalog');q.add_argument('path')
    q=sub.add_parser('import-fixed25');q.add_argument('path')
    q=sub.add_parser('import-graph');q.add_argument('directory');q.add_argument('--manifest-sha',required=True)
    q=sub.add_parser('import-review');q.add_argument('directory');q.add_argument('--manifest-sha',required=True);q.add_argument('--data-member',default='统一人员事件审阅.json');q.add_argument('--delta-member');q.add_argument('--evidence-member',action='append',default=[])
    for command in ('import-identity','import-confirmation','import-archive','import-process','import-video','import-adjacent','import-workflow','import-continuation'):
        q=sub.add_parser(command);q.add_argument('directory');q.add_argument('--manifest-sha',required=True)
    q=sub.add_parser('workflow-list');q.add_argument('--query',default='')
    q=sub.add_parser('workflow-get');q.add_argument('id')
    q=sub.add_parser('workflow-state');q.add_argument('id');q.add_argument('state');q.add_argument('--expect',required=True);q.add_argument('--reason',required=True)
    q=sub.add_parser('workflow-receipt');q.add_argument('id');q.add_argument('path');q.add_argument('--sha',required=True);q.add_argument('--expect',required=True)
    q=sub.add_parser('search');q.add_argument('query')
    q=sub.add_parser('get');q.add_argument('id')
    q=sub.add_parser('history');q.add_argument('id')
    q=sub.add_parser('diff');q.add_argument('id');q.add_argument('--from-version',type=int,required=True);q.add_argument('--to-version',type=int,required=True)
    q=sub.add_parser('job-add');q.add_argument('kind');q.add_argument('--entity');q.add_argument('--detail',required=True);q.add_argument('--priority',type=int,default=5)
    q=sub.add_parser('align-time');q.add_argument('id');q.add_argument('seconds',type=float)
    sub.add_parser('timeline');q=sub.add_parser('graph');q.add_argument('--focus')
    q=sub.add_parser('validate');q.add_argument('--check-files',action='store_true')
    sub.add_parser('duplicates');sub.add_parser('jobs')
    q=sub.add_parser('job-state');q.add_argument('id');q.add_argument('state')
    q=sub.add_parser('grant');q.add_argument('review_json')
    q=sub.add_parser('revoke');q.add_argument('id');q.add_argument('--reason',required=True)
    q=sub.add_parser('export-public');q.add_argument('--destination',required=True);q.add_argument('--output',required=True)
    q=sub.add_parser('export-private');q.add_argument('--output',required=True)
    q=sub.add_parser('backup');q.add_argument('output')
    q=sub.add_parser('restore');q.add_argument('archive');q.add_argument('destination')
    q=sub.add_parser('serve');q.add_argument('--port',type=int,default=18776)
    return p

def main():
    a=parser().parse_args()
    try:
        if a.command=='restore':result=restore(a.archive,a.destination)
        else:
            db=connect(a.db)
            try:
                if a.command=='init':result={'database':str(Path(a.db).resolve()),'schema_version':db.execute('SELECT max(version) FROM schema_migration').fetchone()[0]}
                elif a.command=='import':result=import_json(db,a.input,a.namespace,a.local)
                elif a.command=='import-catalog':result=import_catalog(db,a.path,a.local)
                elif a.command=='import-fixed25':result=import_fixed25(db,a.path,a.local)
                elif a.command=='import-graph':
                    from .graph_adapter import import_graph
                    result=import_graph(db,a.directory,a.manifest_sha,a.local)
                elif a.command=='import-review':
                    from .review import import_review
                    result=import_review(db,a.directory,a.manifest_sha,a.local,a.data_member,a.delta_member,a.evidence_member)
                elif a.command=='import-continuation':
                    from .continuation import import_continuation
                    result=import_continuation(db,a.directory,a.manifest_sha,a.local)
                elif a.command=='import-adjacent':
                    from .adjacent import import_adjacent
                    result=import_adjacent(db,a.directory,a.manifest_sha,a.local)
                elif a.command=='import-workflow':
                    from .workflow import import_workflow
                    result=import_workflow(db,a.directory,a.manifest_sha,a.local)
                elif a.command.startswith('workflow-'):
                    from .workflow import workflow_list,workflow_detail,record_action,import_receipt
                    if a.command=='workflow-list':result=workflow_list(db,a.query)
                    elif a.command=='workflow-get':result=workflow_detail(db,a.id)
                    elif a.command=='workflow-state':result=record_action(db,a.local,a.id,a.state,a.expect,a.reason)
                    else:result=import_receipt(db,a.local,a.id,a.path,a.sha,a.expect)
                elif a.command=='import-video':
                    from .video import import_video
                    result=import_video(db,a.directory,a.manifest_sha,a.local)
                elif a.command=='import-process':
                    from .process import import_process
                    result=import_process(db,a.directory,a.manifest_sha,a.local)
                elif a.command=='import-archive':
                    from .archive import import_archive
                    result=import_archive(db,a.directory,a.manifest_sha,a.local)
                elif a.command in ('import-identity','import-confirmation'):
                    from .identity import import_identity,import_confirmation
                    result=(import_identity if a.command=='import-identity' else import_confirmation)(db,a.directory,a.manifest_sha,a.local)
                elif a.command=='search':result=search(db,a.query)
                elif a.command=='get':result=get_entity(db,a.id)
                elif a.command=='history':result=[dict(r) for r in db.execute('SELECT * FROM revision WHERE entity_id=? ORDER BY version',(a.id,))]
                elif a.command=='diff':
                    import difflib
                    versions=[]
                    for v in (a.from_version,a.to_version):
                        row=db.execute('SELECT body FROM revision WHERE entity_id=? AND version=?',(a.id,v)).fetchone()
                        if not row:raise ValueError('revision not found')
                        versions.append(json.dumps(json.loads(row[0]),ensure_ascii=False,sort_keys=True,indent=2).splitlines())
                    result={'id':a.id,'diff':list(difflib.unified_diff(*versions,fromfile=str(a.from_version),tofile=str(a.to_version),lineterm=''))}
                elif a.command=='job-add':
                    with db:
                        id=enqueue(db,a.kind,a.entity,a.detail,a.priority);audit(db,'job_add',{'id':id,'kind':a.kind})
                    result={'job_id':id}
                elif a.command=='align-time':result=map_local_time(get_entity(db,a.id)['record'],a.seconds)
                elif a.command=='timeline':result=timeline(db)
                elif a.command=='graph':result=graph(db,a.focus)
                elif a.command=='validate':
                    result=validate(db,a.check_files)
                    with db:db.execute('INSERT INTO validation_run(at,report) VALUES(?,?)',(now(),canonical(result)))
                elif a.command=='duplicates':result=duplicate_candidates(db)
                elif a.command=='jobs':result=[dict(r) for r in db.execute('SELECT * FROM job ORDER BY priority,id')]
                elif a.command=='job-state':
                    with db:set_job_state(db,a.id,a.state)
                    result={'updated':a.id,'state':a.state}
                elif a.command=='grant':result={'grant_ids':grant(db,json.loads(Path(a.review_json).read_text()))}
                elif a.command=='revoke':revoke(db,a.id,a.reason);result={'revoked':a.id}
                elif a.command=='export-public':result=write_public(db,a.destination,a.output)
                elif a.command=='export-private':
                    target=Path(a.output).resolve();local=Path(a.local).resolve()
                    if not target.is_relative_to(local):raise ValueError('private export must remain inside --local directory')
                    if target.exists():raise ValueError('refuse overwrite')
                    target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_text(canonical({'format':'p4-evidence-v1','entities':entities(db),'relations':[json.loads(r[0]) for r in db.execute('SELECT body FROM relation ORDER BY id')]}))
                    result={'path':str(target),'sha256':file_hash(target),'note':'current normalized records; full history and raw evidence require backup'}
                elif a.command=='backup':result=backup(db,a.local,a.output)
                elif a.command=='serve':
                    from .server import make_server
                    server=make_server(a.db,a.local,a.port)
                    print('Local private UI: http://127.0.0.1:'+str(server.server_address[1]),flush=True)
                    try:server.serve_forever()
                    except KeyboardInterrupt:pass
                    finally:server.server_close()
                    return 0
            finally:db.close()
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 1 if isinstance(result,dict) and result.get('ok') is False else 0
    except (ValueError,OSError,sqlite3.Error,KeyError) as ex:
        print(json.dumps({'error':str(ex)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
