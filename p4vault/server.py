"""Loopback-only, read-only UI. No uploads, arbitrary files, media, or mutations."""
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .core import *

def read_db(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');return db

def legacy_search(settings,q,mode):
    path=settings.get('legacy_catalog')
    if not path:return {'available':False,'reason':'No legacy catalog configured','results':[]}
    db=read_db(path)
    try:
        escaped='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        if mode=='segments':
            sql="SELECT s.source_id,s.start,s.end,s.text,s.quality,d.path AS document_path FROM segments s JOIN documents d ON s.document_id=d.id WHERE s.text LIKE ? ESCAPE '\\' LIMIT 50"
        else:sql="SELECT id,name,path,kind,publication_hold,verification FROM assets WHERE path LIKE ? ESCAPE '\\' LIMIT 50"
        return {'available':True,'scope':'Read-only inherited catalog, not migrated in full; path counts are not work counts','results':[dict(r) for r in db.execute(sql,(escaped,))]}
    finally:db.close()

def make_server(dbpath,local_dir,port):
    settings_path=Path(local_dir)/'settings.json'
    settings=json.loads(settings_path.read_text()) if settings_path.exists() else {}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,data,mime='application/json; charset=utf-8'):
            raw=data if isinstance(data,bytes) else canonical(data).encode()
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            actual_port=self.server.server_address[1]
            allowed={f'127.0.0.1:{actual_port}',f'localhost:{actual_port}'}
            if self.headers.get('Host') not in allowed:return self.send(403,{'error':'invalid Host'})
            origin=self.headers.get('Origin')
            if origin and origin not in {'http://'+h for h in allowed}:return self.send(403,{'error':'cross-origin request blocked'})
            # A normal address-bar/extension top-level navigation may be cross-site.
            # Only the data-free static shell is public to that navigation. APIs,
            # frames, fetches and requests with a foreign Origin remain blocked.
            top_level_shell=(urlsplit(self.path).path=='/' and not origin and self.headers.get('Sec-Fetch-Mode')=='navigate' and self.headers.get('Sec-Fetch-Dest')=='document')
            if self.headers.get('Sec-Fetch-Site')=='cross-site' and not top_level_shell:return self.send(403,{'error':'cross-site request blocked'})
            parts=urlsplit(self.path);query=parse_qs(parts.query)
            static={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
            if parts.path in static:
                p=ROOT/'web'/static[parts.path];mime={'html':'text/html','js':'text/javascript','css':'text/css'}[p.suffix[1:]]
                return self.send(200,p.read_bytes(),mime+'; charset=utf-8')
            db=read_db(dbpath)
            try:
                arg=lambda name,default='':query.get(name,[default])[0]
                if parts.path in {'/api/workflows','/api/workflow'}:
                    from .workflow import workflow_list,workflow_detail
                    return self.send(200,workflow_detail(db,arg('id')) if parts.path=='/api/workflow' else workflow_list(db,arg('q')))
                if parts.path=='/api/review-frame':
                    from .video import frame_bytes
                    return self.send(200,frame_bytes(db,local_dir,arg('id')),'image/jpeg')
                if parts.path=='/api/video-timeline':
                    from .video import video_timeline
                    return self.send(200,video_timeline(db,arg('id')))
                if parts.path=='/api/preview':
                    e=get_entity(db,arg('id'))['record'];ref=e.get('metadata',{}).get('preview_ref')
                    if not ref:return self.send(404,{'error':'no approved local snapshot preview'})
                    base=(Path(local_dir)/'research_snapshots').resolve();rel=Path(ref['path'])
                    if rel.is_absolute() or '..' in rel.parts:raise ValueError('invalid preview member')
                    preview=base/ref['manifest_sha256']/rel
                    if preview.is_symlink() or not preview.resolve().is_relative_to(base) or preview.suffix.lower() not in {'.jpg','.jpeg','.png'} or preview.stat().st_size>2*1024*1024:raise ValueError('invalid preview target')
                    if file_hash(preview)!=ref['sha256']:raise ValueError('preview hash mismatch')
                    return self.send(200,preview.read_bytes(),'image/png' if preview.suffix.lower()=='.png' else 'image/jpeg')
                if parts.path in {'/api/reviews','/api/review','/api/review-facets'}:
                    from .review import review_search,review_detail,review_facets
                    if parts.path=='/api/review':result=review_detail(db,arg('id'))
                    elif parts.path=='/api/review-facets':result=review_facets(db)
                    else:result=review_search(db,arg('q'),arg('person'),arg('project'),arg('relation'),arg('nature'),arg('entity'))
                elif parts.path in {'/api/identity-cards','/api/identity-record'}:
                    from .identity import identity_search,identity_detail
                    result=identity_detail(db,arg('id')) if parts.path=='/api/identity-record' else identity_search(db,arg('q'),arg('priority')=='1',arg('entity'),arg('context'))
                elif parts.path=='/api/search':result=search(db,arg('q'),kind=arg('kind'))
                elif parts.path=='/api/entity':
                    from .locators import private_locators
                    result=get_entity(db,arg('id'));links=private_locators(db,arg('id'))
                    if links:result['private_locators']=links
                elif parts.path=='/api/history':result=[dict(r) for r in db.execute('SELECT version,digest,changed_at,reason FROM revision WHERE entity_id=? ORDER BY version DESC',(arg('id'),))]
                elif parts.path=='/api/related':
                    focus=arg('id');records=entities(db);ids=set()
                    for e in records:
                        if e['id']==focus:ids.update(e['refs'])
                        elif focus in e['refs']:ids.add(e['id'])
                    for edge in db.execute('SELECT subject,object FROM relation WHERE subject=? OR object=?',(focus,focus)):
                        ids.update(edge)
                    ids.discard(focus)
                    result=[{k:e[k] for k in ('id','kind','title','start','end','source_id','reliability') if k in e} for e in records if e['id'] in ids]
                elif parts.path=='/api/diff':
                    import difflib
                    versions=[]
                    for version in (int(arg('from')),int(arg('to'))):
                        row=db.execute('SELECT body FROM revision WHERE entity_id=? AND version=?',(arg('id'),version)).fetchone()
                        if not row:raise ValueError('revision not found')
                        versions.append(json.dumps(json.loads(row[0]),ensure_ascii=False,sort_keys=True,indent=2).splitlines())
                    result={'from':arg('from'),'to':arg('to'),'lines':list(difflib.unified_diff(*versions,fromfile=arg('from'),tofile=arg('to'),lineterm=''))}
                elif parts.path=='/api/release-check':
                    from .publish import public_projection
                    try:
                        projection=public_projection(db,arg('destination','github-index'))
                        result={'allowed':projection['count']>0,'approved_records':projection['count'],'destination':projection['destination'],'reason':'通过本地校验；尚未导出或上传' if projection['count'] else '未记录针对当前修订与目的地的公开许可。导出将被阻止。','private_fields_included':False,'output_written':False}
                    except ValueError as ex:result={'allowed':False,'reason':str(ex),'output_written':False}
                elif parts.path=='/api/timeline':result=timeline(db)
                elif parts.path=='/api/graph':result=graph(db,arg('focus') or None,min(max(int(arg('limit','120')),1),500))
                elif parts.path=='/api/jobs':result=[dict(r) for r in db.execute("SELECT * FROM job WHERE (?='' OR state=?) AND instr(lower(kind || ' ' || detail),lower(?))>0 ORDER BY priority,id LIMIT 500",(arg('state'),arg('state'),arg('q')))]
                elif parts.path=='/api/status':
                    result={'counts':{r[0]:r[1] for r in db.execute('SELECT kind,count(*) FROM entity GROUP BY kind')},'relations':db.execute('SELECT count(*) FROM relation').fetchone()[0],'revisions':db.execute('SELECT count(*) FROM revision').fetchone()[0],'jobs':{r[0]:r[1] for r in db.execute('SELECT state,count(*) FROM job GROUP BY state')},'grants':db.execute('SELECT count(*) FROM grant_record WHERE revoked_at IS NULL').fetchone()[0],'schema_version':db.execute('SELECT max(version) FROM schema_migration').fetchone()[0],'legacy_catalog_configured':bool(settings.get('legacy_catalog')),'scope':'Local private research. No remote write features.','private_review_records':db.execute('SELECT count(*) FROM review_record').fetchone()[0] if db.execute("SELECT 1 FROM sqlite_master WHERE name='review_record'").fetchone() else 0}
                elif parts.path=='/api/catalog-search':result=legacy_search(settings,arg('q'),arg('mode','assets'))
                else:return self.send(404,{'error':'not found'})
                return self.send(200,result)
            except (ValueError,OSError,sqlite3.Error) as ex:return self.send(400,{'error':str(ex)})
            finally:db.close()
        def do_POST(self):self.send(405,{'error':'read-only server'})
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)
