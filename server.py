import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import hunt

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get('IMAGE_HUNT_PORT') or 8666)

ASSETS = {'/favicon.ico': ('icon.ico', 'image/x-icon'), '/favicon.png': ('favicon.png', 'image/png'),
          '/apple-touch-icon.png': ('apple-touch-icon.png', 'image/png'), '/icon.png': ('icon.png', 'image/png')}
MANIFEST = {'name': 'Image Hunt', 'short_name': 'Image Hunt', 'start_url': '/', 'display': 'standalone',
            'background_color': '#120000', 'theme_color': '#300000',
            'icons': [{'src': '/icon.png', 'sizes': '1024x1024', 'type': 'image/png'},
                      {'src': '/apple-touch-icon.png', 'sizes': '180x180', 'type': 'image/png'}]}

JOBS = {}
JOBS_LOCK = threading.Lock()
JOB_QUEUE = queue.Queue()
WORKER_LOCK = threading.Lock()

CHATS = {}
CHATS_LOCK = threading.Lock()
CHAT_LOCKS = {}
CHAT_LOCKS_GUARD = threading.Lock()


def chat_model_lock(model_id):
    with CHAT_LOCKS_GUARD:
        return CHAT_LOCKS.setdefault(model_id, threading.Lock())


def valid_models():
    return {m['id'] for m in hunt.discover_models().get('models') or []}


def resolve_static(rel):
    if not isinstance(rel, str) or not rel or rel.startswith('/'):
        return None
    path = os.path.normpath(os.path.join(ROOT, rel))
    if path == ROOT or not path.startswith(ROOT + os.sep) or '..' in rel.split(os.sep) or '..' in rel.split('/'):
        return None
    if rel == 'app.html' and os.path.isfile(path):
        return path
    if re.fullmatch(r'saved/[A-Za-z0-9._-]+\.json', rel) and os.path.isfile(path):
        return path
    return None


def saved_entries():
    os.makedirs(hunt.SAVED, exist_ok=True)
    out = []
    for f in sorted(os.listdir(hunt.SAVED)):
        if not f.endswith('.json'):
            continue
        path = os.path.join(hunt.SAVED, f)
        slug = f[:-len('.json')]
        try:
            with open(path) as fh:
                meta, items = hunt.load_items(json.load(fh))
        except Exception:
            continue
        if not meta:
            human = ' '.join(w[0].upper() + w[1:] for w in re.sub(r'[-_]+', ' ', slug).split())
            meta = {'title': human, 'query': human, 'kind': '', 'model': '',
                    'created': time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(path)))}
        out.append({'slug': slug, 'title': meta.get('title') or slug,
                    'query': meta.get('query') or meta.get('title') or slug,
                    'kind': meta.get('kind') or '', 'count': len(items),
                    'created': meta.get('created') or ''})
    out.sort(key=lambda e: e['created'] or '', reverse=True)
    return out


def worker_count():
    try:
        return max(1, int(os.environ.get('IMAGE_HUNT_WORKERS') or 3))
    except (TypeError, ValueError):
        return 3


def job_payload(job):
    payload = dict(job)
    if payload.get('status') != 'done' and not payload.get('partial'):
        payload['items'] = []
    return payload


def ensure_worker():
    with WORKER_LOCK:
        alive = {t.name for t in threading.enumerate() if t.name.startswith('hunt-worker-') and t.is_alive()}
        for i in range(1, worker_count() + 1):
            name = f'hunt-worker-{i}'
            if name not in alive:
                threading.Thread(target=worker, name=name, daemon=True).start()


def worker():
    while True:
        jid = JOB_QUEUE.get()
        job = JOBS[jid]
        with JOBS_LOCK:
            job['status'] = 'running'
        hunt.run(job)


def run_chat_thread(chat, model, effort, message, ctx):
    with chat_model_lock(model):
        payload = {'model': model, 'effort': effort, 'message': message, 'context': ctx}
        try:
            hunt.run_chat(payload)
        except Exception as e:
            payload = {'status': 'error', 'reply': None, 'actions': None,
                       'error': f'{type(e).__name__}: {str(e)[:200]}'}
        with CHATS_LOCK:
            for k in ('status', 'reply', 'actions', 'error'):
                chat[k] = payload.get(k)


def download_dir(slug):
    return os.path.join(os.path.expanduser('~'), 'Pictures', 'image-hunt', hunt.safe_name(slug))


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _foreign(self):
        # Any site open in Safari can send requests to localhost. Refuse other Hosts
        # (DNS rebinding) and non-JSON POSTs (cross-site forms/no-cors fetches can't
        # send application/json without a CORS preflight, which we never grant).
        host = (self.headers.get('Host') or '').split(':')[0]
        if host not in ('127.0.0.1', 'localhost'):
            return True
        return self.command == 'POST' and \
            not (self.headers.get('Content-Type') or '').startswith('application/json')

    def parse_request(self):
        if not super().parse_request():
            return False
        if self._foreign():
            self._json(403, {'error': 'forbidden'})
            return False
        return True

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > 1024 * 1024:
            raise ValueError('body too large')
        raw = self.rfile.read(n) if n else b''
        if not raw:
            return {}
        return json.loads(raw.decode('utf-8'))

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(p.path)
        try:
            if path == '/' or path == '/app.html':
                sp = resolve_static('app.html')
                if sp:
                    with open(sp, 'rb') as f:
                        self._send(200, f.read(), 'text/html; charset=utf-8')
                else:
                    self._send(200, '<!doctype html><meta charset="utf-8"><title>image-hunt</title>'
                               '<body style="background:#120000;color:#fdd;font-family:sans-serif">'
                               '<p>image-hunt backend running — app.html not written yet.</p></body>',
                               'text/html; charset=utf-8')
                return
            if path in ASSETS:
                name, ctype = ASSETS[path]
                with open(os.path.join(ROOT, 'assets', name), 'rb') as f:
                    self._send(200, f.read(), ctype)
                return
            if path == '/manifest.webmanifest':
                self._send(200, json.dumps(MANIFEST), 'application/manifest+json')
                return
            if path.startswith('/saved/'):
                sp = resolve_static(path.lstrip('/'))
                if not sp:
                    self._json(404, {'error': 'not found'})
                    return
                with open(sp, 'rb') as f:
                    self._send(200, f.read(), 'application/json; charset=utf-8')
                return
            if path == '/api/saved':
                self._json(200, saved_entries())
                return
            if path.startswith('/api/saved/'):
                slug = urllib.parse.unquote(path[len('/api/saved/'):])
                sp = resolve_static('saved/' + os.path.basename(slug) + '.json')
                if not sp:
                    self._json(404, {'error': 'not found'})
                    return
                with open(sp) as f:
                    meta, items = hunt.load_items(json.load(f))
                if not meta:
                    slug_c = os.path.basename(slug)
                    human = ' '.join(w[0].upper() + w[1:] for w in re.sub(r'[-_]+', ' ', slug_c).split())
                    meta = {'slug': slug_c, 'title': human, 'query': human, 'kind': '',
                            'model': '', 'created': time.strftime(
                                '%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(sp))),
                            'count': len(items)}
                self._json(200, {'meta': meta, 'items': items})
                return
            if path == '/api/models':
                refresh = urllib.parse.parse_qs(p.query).get('refresh') == ['1']
                self._json(200, hunt.discover_models(refresh=refresh))
                return
            if path == '/api/sources':
                self._json(200, hunt.sources_info())
                return
            if path.startswith('/api/chat/'):
                cid = path[len('/api/chat/'):]
                with CHATS_LOCK:
                    chat = dict(CHATS.get(cid) or {})
                if not chat:
                    self._json(404, {'error': 'no such chat'})
                    return
                self._json(200, {k: chat.get(k) for k in ('status', 'reply', 'actions', 'error')})
                return
            if path == '/api/jobs':
                with JOBS_LOCK:
                    jobs = [dict(j) for j in JOBS.values()
                            if j.get('status') in ('queued', 'running')]
                    jobs.sort(key=lambda j: j.get('id') or '')
                self._json(200, [{'id': j['id'], 'query': j.get('query'), 'kind': j.get('kind'),
                                  'model': j.get('model'), 'status': j.get('status'),
                                  'stage': j.get('stage') or 'queued',
                                  'stage_text': j.get('stage_text') or '',
                                  'progress': j.get('progress')} for j in jobs])
                return
            if path.startswith('/api/jobs/'):
                jid = path[len('/api/jobs/'):]
                with JOBS_LOCK:
                    job = dict(JOBS.get(jid) or {})
                if not job:
                    self._json(404, {'error': 'no such job'})
                    return
                self._json(200, job_payload(job))
                return
            self._json(404, {'error': 'not found'})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self._json(500, {'error': f'{type(e).__name__}: {e}'})

    def do_DELETE(self):
        p = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(p.path)
        if path.startswith('/api/sources/'):
            name = os.path.basename(urllib.parse.unquote(path[len('/api/sources/'):]))
            if not name or name.startswith('.'):
                self._json(400, {'error': 'bad source name'})
                return
            if not hunt.delete_custom_source(name):
                self._json(404, {'error': 'no such custom source'})
                return
            self._json(200, {'ok': True})
            return
        if path.startswith('/api/saved/'):
            slug = os.path.basename(urllib.parse.unquote(path[len('/api/saved/'):]))
            if not slug or slug.startswith('.'):
                self._json(400, {'error': 'bad slug'})
                return
            sp = resolve_static('saved/' + slug + '.json')
            if not sp:
                self._json(404, {'error': 'not found'})
                return
            try:
                os.remove(sp)
                self._json(200, {'ok': True})
            except OSError as e:
                self._json(500, {'error': str(e)})
            return
        self._json(404, {'error': 'not found'})

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(p.path)
        try:
            if path == '/api/sources':
                body = self._body()
                spec = body.get('spec') if isinstance(body.get('spec'), dict) else body
                err = hunt.source_spec_error(spec)
                if err:
                    self._json(400, {'error': err})
                    return
                spec = hunt.validate_source_spec(spec)
                test = ' '.join(str(body.get('query') or body.get('test') or spec['label']).split())
                hits = [h for h in hunt.custom_search(spec, test or 'art', 6)
                        if h.get('url') and h.get('thumb')]
                if not hits:
                    self._json(422, {'error': 'test search returned no items with url+thumb'})
                    return
                try:
                    spec = hunt.save_custom_source(spec)
                except ValueError as e:
                    self._json(400, {'error': str(e)})
                    return
                self._json(200, {'ok': True, 'name': spec['name'], 'spec': spec,
                                 'hits': hits[:4]})
                return
            if path == '/api/keys':
                body = self._body()
                if not isinstance(body, dict):
                    self._json(400, {'error': 'keys must be an object'})
                    return
                try:
                    saved = hunt.save_keys(body)
                except ValueError as e:
                    self._json(400, {'error': str(e)})
                    return
                self._json(200, {'ok': True, 'saved': saved})
                return
            if path == '/api/chat':
                body = self._body()
                model = str(body.get('model') or '')
                if model == 'none' or model not in valid_models():
                    self._json(400, {'error': 'choose a chat model'})
                    return
                message = str(body.get('message') or '').strip()
                if not message:
                    self._json(400, {'error': 'message required'})
                    return
                ctx = body.get('context') if isinstance(body.get('context'), dict) else {}
                effort = str(body.get('effort') or 'default')
                cid = uuid.uuid4().hex[:12]
                chat = {'id': cid, 'model': model, 'status': 'running',
                        'reply': None, 'actions': None, 'error': None}
                with CHATS_LOCK:
                    CHATS[cid] = chat
                threading.Thread(target=run_chat_thread,
                                 args=(chat, model, effort, message, ctx),
                                 daemon=True).start()
                self._json(200, {'chat': cid})
                return
            if path == '/api/chat/reset':
                body = self._body()
                model = str(body.get('model') or '')
                if model == 'none' or model not in valid_models():
                    self._json(400, {'error': 'choose a chat model'})
                    return
                self._json(200, hunt.reset_chat(model))
                return
            if path == '/api/search':
                body = self._body()
                query = ' '.join(str(body.get('query') or '').split())
                if not query:
                    self._json(400, {'error': 'query required'})
                    return
                mode = str(body.get('mode') or body.get('kind') or 'anything')
                if mode not in hunt.MODES and mode not in hunt.LEGACY_MODES:
                    self._json(400, {'error': 'mode must be ' + '|'.join(hunt.MODES)})
                    return
                mode = hunt.canonical_mode(mode)
                picked = body.get('sources')
                sources_in = [str(s) for s in picked if isinstance(s, str)][:60] \
                    if isinstance(picked, list) else None
                model = str(body.get('model') or hunt.discover_models().get('default') or 'none')
                if model != 'none' and model not in valid_models():
                    self._json(400, {'error': 'unknown model'})
                    return
                effort = str(body.get('effort') or 'default')
                limit = int(body.get('limit') or 200)
                jid = uuid.uuid4().hex[:12]
                job = {'id': jid, 'query': query, 'kind': mode, 'mode': mode, 'model': model,
                       'sources': sources_in,
                       'effort': effort, 'linework_only': bool(body.get('linework_only')),
                       'limit': limit, 'status': 'queued', 'stage': 'queued',
                       'stage_text': 'Queued…', 'progress': None, 'log': [],
                       'items': [], 'partial': False, 'slug': None, 'error': None}
                with JOBS_LOCK:
                    JOBS[jid] = job
                ensure_worker()
                JOB_QUEUE.put(jid)
                self._json(200, {'job': jid})
                return
            if path == '/api/download':
                body = self._body()
                url = str(body.get('url') or '')
                parsed = urllib.parse.urlparse(url)
                if parsed.scheme != 'https':
                    self._json(400, {'error': 'url must be https'})
                    return
                slug = str(body.get('slug') or 'default')
                name = hunt.safe_name(body.get('name') or
                                      os.path.basename(urllib.parse.unquote(parsed.path)))
                target_dir = download_dir(slug)
                os.makedirs(target_dir, exist_ok=True)
                target = os.path.join(target_dir, name)
                req = urllib.request.Request(url, headers=hunt.UA)
                with urllib.request.urlopen(req, timeout=120) as r, open(target, 'wb') as f:
                    f.write(r.read())
                self._json(200, {'ok': True, 'path': target})
                return
            if path == '/api/reveal':
                body = self._body()
                d = download_dir(str(body.get('slug') or ''))
                if not os.path.isdir(d):
                    self._json(404, {'error': 'folder not created yet — download something first'})
                    return
                if os.name == 'nt':
                    os.startfile(d)
                else:
                    subprocess.run(['open', '-R', d] if sys.platform == 'darwin' else ['xdg-open', d])
                self._json(200, {'ok': True})
                return
            self._json(404, {'error': 'not found'})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except urllib.error.HTTPError as e:
            self._json(502, {'error': f'download failed: HTTP {e.code}'})
        except Exception as e:
            self._json(500, {'error': f'{type(e).__name__}: {e}'})

    def log_message(self, fmt, *args):
        print(f'[{time.strftime("%H:%M:%S")}] {fmt % args}', flush=True)


def serve():
    os.makedirs(hunt.SAVED, exist_ok=True)
    ensure_worker()
    httpd = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print(f'image-hunt on http://127.0.0.1:{PORT}/', flush=True)
    httpd.serve_forever()


if __name__ == '__main__':
    serve()
