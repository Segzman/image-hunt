import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import concurrent.futures as cf

ROOT = os.path.dirname(os.path.abspath(__file__))
SAVED = os.path.join(ROOT, 'saved')
AGENT_DIR = os.path.join(ROOT, '.agent')
HELPER_SANDBOX = os.path.join(ROOT, '.helper-sandbox')


def find_ag():
    # IMAGE_HUNT_AG > ag on PATH > the installer's / a clone's usual spots. None = scripts-only.
    if os.name != 'posix':
        return None  # ag is POSIX-only (macOS/Linux)
    for c in (os.environ.get('IMAGE_HUNT_AG'), shutil.which('ag'),
              os.path.expanduser('~/.local/bin/ag'), os.path.expanduser('~/agent-cli/ag')):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


AG_BIN = find_ag()

UA = {'User-Agent': 'image-hunt/1.0 (personal use)'}
ICONIFY = 'https://api.iconify.design'
OPENVERSE = 'https://api.openverse.org/v1/images/'
COMMONS = 'https://commons.wikimedia.org/w/api.php'

DEFAULT_MODEL = 'opencode/glm-5.3-flash-free'
NONE_MODEL = {'id': 'none', 'label': 'No AI — scripts only', 'backend': None, 'data': None,
              'efforts': ['default']}
MODELS = [
    NONE_MODEL,
    {'id': 'opencode/glm-5.3-flash-free', 'label': 'GLM 5.3 Flash (free)', 'backend': 'opencode',
     'data': 'train', 'efforts': ['default', 'low', 'high', 'max']},
    {'id': 'opencode/space-bunny-free', 'label': 'Space Bunny (free)', 'backend': 'opencode',
     'data': 'zdr', 'efforts': ['default']},
    {'id': 'opencode/step-5-preview-free', 'label': 'Step 5 Preview (free)', 'backend': 'opencode',
     'data': 'zdr', 'efforts': ['default']},
    {'id': 'claude/haiku', 'label': 'Claude Haiku', 'backend': 'claude', 'data': 'retain',
     'efforts': ['default', 'low', 'medium', 'high', 'max']},
    {'id': 'claude/sonnet', 'label': 'Claude Sonnet', 'backend': 'claude', 'data': 'retain',
     'efforts': ['default', 'low', 'medium', 'high', 'max']},
    {'id': 'claude/opus', 'label': 'Claude Opus', 'backend': 'claude', 'data': 'retain',
     'efforts': ['default', 'low', 'medium', 'high', 'max']},
]

try:
    from PIL import Image, ImageStat
except Exception:
    Image = None
    ImageStat = None

def log(job, msg):
    stamp = time.strftime('%H:%M:%S')
    job.setdefault('log', []).append(f'[{stamp}] {msg}')


def stage(job, name, text, progress=None):
    job['stage'] = name
    job['stage_text'] = text
    job['progress'] = list(progress) if progress is not None else None


def extract_json(text):
    if not isinstance(text, str):
        return None

    def scan(opener, closer):
        start = text.find(opener)
        while start != -1:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(text)):
                c = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif c == '\\':
                        esc = True
                    elif c == '"':
                        in_str = False
                elif c == '"':
                    in_str = True
                elif c == opener:
                    depth += 1
                elif c == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return start, json.loads(text[start:i + 1])
                        except Exception:
                            break
            start = text.find(opener, start + 1)
        return None

    hits = [r for r in (scan(o, c) for o, c in (('{', '}'), ('[', ']'))) if r]
    return min(hits, key=lambda t: t[0])[1] if hits else None


def strip_qs(url):
    return (url or '').split('?', 1)[0]


def retile(thumb, px):
    if not thumb:
        return ''
    return re.sub(r'\d+px-', f'{px}px-', thumb, count=1)


def safe_name(name):
    name = (name or '').strip()
    name = re.sub(r'[/\\]+', '_', name)
    name = name.replace('\x00', '').strip('. ')
    name = re.sub(r'\s+', ' ', name)
    if not name:
        name = 'image'
    return name[:120].strip('. ') or 'image'


def slugify(text, taken=()):
    base = re.sub(r'[^a-z0-9]+', '-', (text or '').lower()).strip('-')[:50].strip('-')
    if not base:
        base = 'search'
    slug = base
    n = 2
    while slug in taken:
        slug = f'{base}-{n}'
        n += 1
    return slug


def load_items(d):
    if isinstance(d, list):
        items = [i for i in d if isinstance(i, dict)]
        meta = {}
    elif isinstance(d, dict):
        meta = d.get('meta') if isinstance(d.get('meta'), dict) else {}
        items = [i for i in (d.get('items') or []) if isinstance(i, dict)]
    else:
        return {}, []
    return meta, items


_http_lock = threading.Lock()
_commons_next = [0.0]


def _get_json(url, timeout):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace').strip() or 'null')


def commons_api(params, timeout=30):
    url = COMMONS + '?' + urllib.parse.urlencode(params)
    with _http_lock:
        wait = _commons_next[0] - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            d = _get_json(url, timeout)
            _commons_next[0] = time.monotonic() + 4
            return d
        except Exception:
            time.sleep(20)
            try:
                d = _get_json(url, timeout)
                _commons_next[0] = time.monotonic() + 4
                return d
            except Exception as e2:
                _commons_next[0] = time.monotonic() + 4
                raise RuntimeError(f'commons failed twice: {e2}') from e2


def commons_items(d, src, tag):
    out = []
    pages = (((d or {}).get('query') or {}).get('pages') or {})
    for p in pages.values():
        ii = (p.get('imageinfo') or [{}])[0]
        u = strip_qs(ii.get('url', ''))
        th = strip_qs(ii.get('thumburl') or '')
        low = u.lower()
        if not low.endswith(('.jpg', '.jpeg', '.png', '.gif', '.tif', '.tiff')):
            continue
        w = int(ii.get('width') or 0)
        h = int(ii.get('height') or 0)
        kind = 'image'
        if low.endswith('.svg'):
            kind = 'art'
        elif low.endswith(('.tif', '.tiff')):
            u = retile(th, min(w or 3000, 3000))
            if not u:
                continue
        out.append({'title': (p.get('title') or '')[len('File:'):] or p.get('title'),
                    'url': u, 'thumb': th or u, 'page': strip_qs(ii.get('descriptionurl', '')),
                    'src': src, 'lic': 'PD/see page', 'w': w, 'h': h,
                    'tag': tag, 'kind': kind})
    return out


def commons_search(q, tag, bitmap=False):
    gsr = q + (' filetype:bitmap' if bitmap else '')
    d = commons_api({'action': 'query', 'format': 'json', 'generator': 'search',
                     'gsrsearch': gsr, 'gsrnamespace': '6', 'gsrlimit': '50',
                     'prop': 'imageinfo', 'iiprop': 'url|size', 'iiurlwidth': '500'})
    return commons_items(d, 'Commons', tag)


def commons_categories(names, tag):
    out = []
    for name in names:
        name = re.sub(r'[\s_]+', ' ', str(name or '')).strip()
        if not name:
            continue
        d = commons_api({'action': 'query', 'format': 'json',
                         'generator': 'categorymembers', 'gcmtitle': 'Category:' + name,
                         'gcmtype': 'file', 'gcmlimit': '50',
                         'prop': 'imageinfo', 'iiprop': 'url|size', 'iiurlwidth': '500'})
        out.extend(commons_items(d, 'Commons category', tag))
    return out


def openverse(q, tag, svg=False):
    params = {'q': q, 'page_size': '20', 'mature': 'false'}
    if svg:
        params['extension'] = 'svg'
    d = _get_json(OPENVERSE + '?' + urllib.parse.urlencode(params), 150 if not svg else 90)
    out = []
    for r in (d.get('results') or []):
        w = r.get('width')
        h = r.get('height')
        lic = r.get('license') or ''
        ver = r.get('license_version') or ''
        out.append({'title': r.get('title') or 'openverse image',
                    'url': r.get('url', ''), 'thumb': r.get('thumbnail') or None,
                    'page': r.get('foreign_landing_url', ''),
                    'src': 'Openverse · ' + (r.get('source') or 'openverse'),
                    'lic': f'{lic.upper()} {ver}'.strip(), 'w': w, 'h': h,
                    'tag': tag, 'kind': 'art' if svg else 'image'})
    return [i for i in out if i['url']]


_iconify_lic_cache = {}


def iconify_search(q, tag, limit=96):
    d = _get_json(f'{ICONIFY}/search?query={urllib.parse.quote(q)}&limit={limit}', 30)
    names = [n for n in (d.get('icons') or []) if isinstance(n, str)]
    if not names:
        return []
    prefixes = []
    for name in names:
        pfx, _, icon = name.partition(':')
        if pfx and pfx not in prefixes:
            prefixes.append(pfx)
    lics = iconify_licences(prefixes, d.get('collections'))
    out = []
    for name in names:
        pfx, _, icon = name.partition(':')
        if not pfx or not icon:
            continue
        info = lics.get(pfx, {})
        out.append({'title': f'{pfx}:{icon}',
                    'url': f'{ICONIFY}/{pfx}/{icon}.svg?height=1024',
                    'thumb': None,
                    'page': f'https://icon-sets.iconify.design/{pfx}/{icon}/',
                    'src': 'Iconify · ' + (info.get('name') or pfx),
                    'lic': info.get('lic') or 'see page',
                    'tag': tag, 'kind': 'icon'})
    return out


def iconify_licences(prefixes, collections=None):
    merged = dict(_iconify_lic_cache)
    for pfx, info in {str(k): v for k, v in ((collections or {}).items())}.items():
        if isinstance(info, dict) and pfx not in merged:
            lic = ((info.get('license') or {}).get('spdx')
                   or (info.get('license') or {}).get('title') or '')
            merged[pfx] = {'name': info.get('name') or pfx, 'lic': lic}
            _iconify_lic_cache[pfx] = merged[pfx]
    missing = [p for p in prefixes if p not in _iconify_lic_cache]
    if missing:
        try:
            d = _get_json(ICONIFY + '/collections?prefixes=' + ','.join(missing), 30)
            for pfx, info in (d or {}).items():
                if not isinstance(info, dict):
                    continue
                lic = ((info.get('license') or {}).get('spdx')
                       or (info.get('license') or {}).get('title') or '')
                _iconify_lic_cache[pfx] = {'name': info.get('name') or pfx, 'lic': lic}
        except Exception:
            pass
    merged = dict(_iconify_lic_cache)
    return {p: merged.get(p, {'name': p, 'lic': ''}) for p in prefixes}


def fetch_items(kind, queries, icons, categories, job):
    tasks = []
    if kind == 'lineart':
        for n in icons:
            tasks.append((f'iconify:{n}', lambda t=n: iconify_search(t, t)))
        for q in queries:
            tasks.append(('openverse-svg', lambda t=q: openverse(t, t, svg=True)))
    elif kind == 'match':
        for q in queries:
            tasks.append(('commons', lambda t=q: commons_search(t, t, bitmap=True)))
            tasks.append(('openverse', lambda t=q: openverse(t, t)))
        for c in categories:
            tasks.append(('commons-cat', lambda t=c: commons_categories([t], t)))
    else:
        for q in queries:
            tasks.append(('openverse', lambda t=q: openverse(t, t)))
            tasks.append(('commons', lambda t=q: commons_search(t, t)))

    by_src = {}
    total = len(tasks)
    if total:
        stage(job, 'fetching', f'Searching sources (0/{total})…', [0, total])
    with cf.ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(fn, *args): label for label, fn, *args in tasks}
        done = 0
        for fut in cf.as_completed(futs):
            label = futs[fut]
            done += 1
            stage(job, 'fetching', f'Searching sources ({done}/{total})…', [done, total])
            try:
                items = fut.result()
                by_src.setdefault(label, []).extend(items)
            except Exception as e:
                log(job, f'source {label} skipped: {type(e).__name__}: {str(e)[:120]}')
    for label, items in by_src.items():
        log(job, f'{label}: {len(items)} hits')
    all_items = [i for _, items in sorted(by_src.items()) for i in items]

    seen, deduped = set(), []
    for i in all_items:
        if not i["url"] or i["url"] in seen:
            continue
        seen.add(i["url"])
        deduped.append(i)
    kept = []
    for i in deduped:
        if i.get('kind') in ('icon', 'art') or i['url'].lower().endswith('.svg'):
            kept.append(i)
        elif not i.get('w') or int(i.get('w') or 0) >= 600:
            kept.append(i)
    dropped = len(deduped) - len(kept)
    if dropped:
        log(job, f'{dropped} too small (width < 600) dropped')
    return kept


def score_item(item):
    if Image is None:
        return None
    target = item.get('thumb') or item.get('url') or ''
    if not target:
        return None
    for attempt in range(3):
        try:
            req = urllib.request.Request(target, headers=UA)
            data = urllib.request.urlopen(req, timeout=40).read()
            break
        except Exception:
            time.sleep(2 + attempt * 3)
    else:
        return None
    im = Image.open(io.BytesIO(data)).convert('RGB')
    im.thumbnail((256, 256))
    sat = ImageStat.Stat(im.convert('HSV').split()[1]).mean[0] / 255
    g = list(im.convert('L').getdata())
    n = len(g) or 1
    ink = sum(1 for v in g if v < 90) / n
    paper = sum(1 for v in g if v > 170) / n
    mid = 1 - ink - paper
    line = round(max(0.0, (1 - mid) * min(1, ink * 6) * (1 - min(1, sat * 2.5))), 3)
    item['line'] = line
    return item


def score_items(job, items, drop_weak):
    if Image is None:
        log(job, 'Pillow unavailable — linework scoring skipped')
        stage(job, 'scoring', 'Linework scoring skipped (no Pillow).')
        return items
    do = [i for i in items if i.get('kind') != 'icon']
    stage(job, 'scoring', f'Scoring {len(do)} images for ink linework…', [0, len(do)])
    log(job, f'scoring {len(do)} items for linework…')
    results = [None] * len(do)
    done = 0
    with cf.ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(score_item, it): idx for idx, it in enumerate(do)}
        for fut in cf.as_completed(futs):
            try:
                results[futs[fut]] = fut.result()
            except Exception:
                results[futs[fut]] = None
            done += 1
            stage(job, 'scoring', f'Scoring {len(do)} images for ink linework…', [done, len(do)])
    scored = sum(1 for r in results if r is not None)
    log(job, f'{scored}/{len(do)} scored')
    if drop_weak:
        keep = [i for i in items if i.get('kind') == 'icon' or (i.get('line') or 0) >= 0.2]
        dropped = len(items) - len(keep)
        if dropped:
            log(job, f'{dropped} below line threshold 0.2 dropped')
        items = keep
    items.sort(key=lambda i: -(i.get('line') or 0))
    return items


def _ag(args, timeout=30):
    if not AG_BIN:
        raise RuntimeError('ag not installed (AI off) — rerun the installer or set IMAGE_HUNT_AG')
    return subprocess.run([AG_BIN, '--dir', AGENT_DIR, *args], capture_output=True,
                          text=True, timeout=timeout)


BACKEND_ORDER = ('claude', 'codex', 'opencode', 'gemini', 'cursor')
_MODELS_CACHE = {'at': 0.0, 'data': None}
_MODELS_LOCK = threading.Lock()
_MODELS_TTL = 600.0


def discover_models(refresh=False):
    # AI + models come from the user's own harnesses (ag agents doctor / ag models --rank).
    # One discovery at a time, cached 10 min; any ag failure falls back to MODELS. Never raises.
    with _MODELS_LOCK:
        data = _MODELS_CACHE['data']
        if not refresh and data and time.monotonic() - _MODELS_CACHE['at'] < _MODELS_TTL:
            return data
        try:
            data = _discover_now()
        except Exception as e:  # paranoia: the fallback path itself must not raise
            data = {'default': 'none', 'models': _fallback_models(None),
                    'backends': [], 'ag': AG_BIN,
                    'error': f'{type(e).__name__}: {str(e)[:120]}'}
        _MODELS_CACHE['data'] = data
        _MODELS_CACHE['at'] = time.monotonic()
        return data


def _discover_now():
    if not AG_BIN:
        return {'default': 'none', 'models': [dict(NONE_MODEL)], 'backends': [], 'ag': None}
    err = None
    backends, usable, doctor_ok = [], None, False
    try:
        r = _ag(['agents', 'doctor', '--json'], timeout=30)
        rows = ((json.loads(r.stdout).get('data') or {}).get('backends')) or []
        by = {str(row['backend']): row for row in rows
              if isinstance(row, dict) and row.get('backend')}
        for b in BACKEND_ORDER:
            row = by.get(b) or {}
            installed = row.get('installed') is True
            ok = installed and row.get('auth') != 'no'
            backends.append({'backend': b, 'installed': installed, 'auth': row.get('auth'),
                             'version': row.get('version'), 'usable': ok})
            if ok:
                usable = usable if usable is not None else set()
                usable.add(b)
        doctor_ok = True
    except Exception as e:
        err = f'doctor: {type(e).__name__}: {str(e)[:120]}'
    if doctor_ok:
        usable = usable or set()
    models = None
    if usable:
        try:
            models = _rank_models(usable)
        except Exception as e:
            msg = f'models: {type(e).__name__}: {str(e)[:120]}'
            err = f'{err}; {msg}' if err else msg
    if models is None:  # rank failed/missing → today's static list, minus unusable backends
        models = _fallback_models(usable)
    else:
        models = [dict(NONE_MODEL)] + models
    ids = {m['id'] for m in models}
    if DEFAULT_MODEL in ids:
        default = DEFAULT_MODEL
    else:
        default = next((m['id'] for m in models
                        if m['backend'] == 'opencode' and (m.get('data') or '') == 'zdr'), None) \
            or next((m['id'] for m in models if m['backend']), 'none')
    out = {'default': default, 'models': models, 'backends': backends, 'ag': AG_BIN}
    if err:
        out['error'] = err
    return out


def _rank_models(usable):
    r = _ag(['models', '--rank', '--json'], timeout=60)
    rows = ((json.loads(r.stdout).get('data') or {}).get('models')) or []
    out, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        b = str(row.get('backend') or '')
        m = str(row.get('id') or '')
        if b not in usable or not m or (b == 'opencode' and not row.get('free')) \
                or (b + '/' + m if not m.startswith(b + '/') else m) in seen:
            continue
        mid = m if m.startswith(b + '/') else f'{b}/{m}'
        seen.add(mid)
        variants, got = [], set()
        for v in (row.get('variants') or []):
            v = str(v)
            if v and v != 'default' and v not in got:
                got.add(v)
                variants.append(v)
        out.append({'id': mid, 'backend': b, 'model': m,
                    'label': (m[len('opencode/'):] if m.startswith('opencode/') else m) +
                             (' (free)' if row.get('free') else ''),
                    'data': row.get('data') or 'unknown',
                    'efforts': ['default'] + variants,
                    'free': bool(row.get('free')), 'tier': row.get('tier'),
                    '_score': row.get('score')})
    out.sort(key=lambda it: (BACKEND_ORDER.index(it['backend']), it['_score'] is None,
                             -(it['_score'] or 0)))
    for it in out:
        del it['_score']
    return out


def _fallback_models(usable):
    return [dict(m) for m in MODELS
            if m is NONE_MODEL or usable is None or m.get('backend') in usable]


def model_backend(model_id):
    mid = model_id or 'none'
    for m in discover_models().get('models') or []:
        if m.get('id') == mid and m.get('backend'):
            return m['backend'], m.get('model') or mid
    if '/' in mid:
        b, _, m = mid.partition('/')
        return b, m
    return None, mid


def helper_name(model_id):
    return 'helper-' + re.sub(r'[^a-z0-9]+', '-', (model_id or '').lower()).strip('-')


def chat_name(model_id):
    return 'chat-' + re.sub(r'[^a-z0-9]+', '-', (model_id or '').lower()).strip('-')


def model_label(model_id):
    for m in discover_models().get('models') or []:
        if m.get('id') == model_id:
            return m.get('label') or 'AI'
    return 'AI'


def _agent_names():
    r = _ag(['agents', 'list', '--json'])
    listing = json.loads(r.stdout)
    if isinstance(listing, dict) and isinstance(listing.get('data'), dict):
        listing = listing['data']  # ag --json wraps output: {"ok", "data": {"agents": [...]}}
    names = set()
    for a in (listing if isinstance(listing, list) else (listing.get('agents') or [])):
        if isinstance(a, dict) and a.get('name'):
            names.add(a['name'])
    return names


def _ensure_agent(model_id, name):
    # AI helpers/chat agents read untrusted web titles: keep them read-only in an
    # empty dir so they never see the project tree.
    created = False
    if name in _agent_names():
        return created
    backend, model = model_backend(model_id)
    if not (backend and model):
        raise RuntimeError(f'unknown model: {model_id!r}')
    os.makedirs(HELPER_SANDBOX, exist_ok=True)
    r = _ag(['agents', 'add', name, '--backend', backend, '--model', model,
             '--data', 'public', '--mode', 'ro', '--dir', HELPER_SANDBOX])
    if r.returncode != 0:
        raise RuntimeError(f'agents add failed: {(r.stderr or r.stdout).strip()[:160]}')
    created = True
    return created


def ask_helper(model_id, effort, prompt, job):
    if model_id == 'none' or not model_id:
        return ''
    name = helper_name(model_id)
    log(job, f'asking AI helper {name}…')
    try:
        if _ensure_agent(model_id, name):
            log(job, f'helper {name} created')
        if effort and effort != 'default':
            _ag(['agents', 'set', name, '--effort', effort])
        r = _ag(['chat', 'send', name, prompt, '--json', '--timeout', '240'], timeout=250)
        out = json.loads(r.stdout)
        if out.get('ok') is False:
            raise RuntimeError(str(out)[:200])
        data = out.get('data') or {}
        reply = data.get('reply') if isinstance(data, dict) else None
        return reply if isinstance(reply, str) else json.dumps(data)[:4000]
    except Exception as e:
        log(job, f'helper failed ({type(e).__name__}: {str(e)[:120]}) — continuing without AI')
        return ''


KIND_TEXT = {
    'images': 'photographs, memes and other raster images',
    'lineart': 'clean line art: monochrome SVG icons and colourful SVG illustrations',
    'match': 'woodcut / engraving style vintage ink illustrations',
}


def plan(model_id, effort, query, kind, job):
    fallback = {'queries': [query],
                'icons': [w for w in query.split() if len(w) > 2][:4],
                'commons_categories': []}
    if model_id == 'none' or not model_id:
        log(job, f'queries: {fallback["queries"]}')
        return fallback
    prompt = (
        f'You help find poster-ready images. Search phrase: "{query}". Wanted: {KIND_TEXT.get(kind, "images")}.\n'
        f'Craft better search terms. Reply with ONLY JSON, no prose or markdown fences, shaped exactly:\n'
        f'{{"queries": [6-10 search phrases],\n'
        f' "icons": [single noun words for icon search],\n'
        f' "commons_categories": [0-5 Wikimedia Commons category names without the "Category:" prefix]}}\n'
        f'Keep "icons" empty if kind is not lineart. Keep entries short and in English.'
    )
    reply = ask_helper(model_id, effort, prompt, job)
    d = extract_json(reply)
    if not isinstance(d, dict):
        log(job, 'plan fallback (no usable JSON)')
        log(job, f'queries: {fallback["queries"]}')
        return fallback
    plan_d = {'queries': fallback['queries'], 'icons': [], 'commons_categories': []}
    for key, cap in (('queries', 10), ('icons', 4), ('commons_categories', 5)):
        vals = d.get(key)
        if isinstance(vals, list):
            plan_d[key] = [str(v).strip()[:120] for v in vals
                           if isinstance(v, str) and v.strip()][:cap]
    if not plan_d['queries']:
        plan_d['queries'] = fallback['queries']
    if kind != 'lineart':
        plan_d['icons'] = []
    log(job, f'queries: {plan_d["queries"]} · icons: {plan_d["icons"]} · '
             f'categories: {plan_d["commons_categories"]}')
    return plan_d


def curate(model_id, effort, query, items, job):
    if model_id == 'none' or not model_id or not items:
        return items
    stage(job, 'curating', f'{model_label(model_id)} is picking the best ones…')
    lines = []
    for i, it in enumerate(items[:300]):
        title = re.sub(r'\s+', ' ', str(it.get('title') or ''))[:150]
        lines.append(f'{i} | {title} | {it.get("src") or ""}')
    prompt = (
        f'User searched for: "{query}". Judging which search results are genuinely relevant.\n'
        f'Reply with ONLY JSON: {{"picks": [{{"i": <index>, "why": "<=8 words"}}]}} — '
        f'pick only items a human would want; 0 to 60 picks.\n\n' + '\n'.join(lines)
    )
    reply = ask_helper(model_id, effort, prompt, job)
    d = extract_json(reply)
    picks = d.get('picks') if isinstance(d, dict) and isinstance(d.get('picks'), list) else []
    n = 0
    for p in picks:
        try:
            idx = int(p.get('i'))
            why = ' '.join(str(p.get('why', '')).split())[:60]
        except Exception:
            continue
        if 0 <= idx < len(items):
            items[idx]['pick'] = True
            items[idx]['why'] = why
            n += 1
    if n:
        items.sort(key=lambda i: not i.get('pick'))
        log(job, f'AI picked {n} items')
    else:
        log(job, 'AI picked nothing (kept all)')
    return items


CHAT_PREAMBLE = (
    'You help a user find poster-ready images for art projects: memes, public-domain '
    'art, line-art SVGs, woodcut-style vintage illustrations. Be concrete: suggest '
    'specific subjects, eras, artists, styles and sources that are public-domain '
    'friendly.\n'
    'When you propose searches, END your reply with a fenced code block labelled '
    '"searches" holding a JSON array of 1-5 objects shaped exactly:\n'
    '[{"query": "search phrase", "kind": "images|lineart|match", "linework_only": false, "why": "short reason"}]\n'
    'Example:\n'
    '```searches\n'
    '[{"query":"halloween woodcut bat","kind":"match","linework_only":true,"why":"vintage ink bats"}]\n'
    '```\n'
    'Never put the searches fence more than once. Reply text stays plain prose outside the block.'
)


def parse_chat_reply(text):
    reply = (text or '').strip()
    actions = []
    m = re.search(r'```searches[ \t]*\r?\n(.*?)\r?\n?```', reply, re.S)
    if m:
        raw = m.group(1)
        parsed = extract_json(raw)
        if isinstance(parsed, list):
            for a in parsed:
                if not isinstance(a, dict):
                    continue
                q = str(a.get('query') or '').strip()
                if not q:
                    continue
                kind = a.get('kind')
                actions.append({'query': ' '.join(q.split())[:120],
                                'kind': kind if kind in ('images', 'lineart', 'match') else 'images',
                                'linework_only': bool(a.get('linework_only')),
                                'why': ' '.join(str(a.get('why') or '').split())[:80]})
                if len(actions) >= 5:
                    break
        reply = (reply[:m.start()] + reply[m.end():]).strip()
    return reply, actions


def chat_prompt(message, ctx):
    ctx = ctx if isinstance(ctx, dict) else {}
    parts = []
    slug = str(ctx.get('slug') or '').strip()
    query = str(ctx.get('query') or '').strip()
    kind = str(ctx.get('kind') or '').strip()
    if slug:
        parts.append(f'slug={slug}')
    if query or kind:
        parts.append(f'query={query!r} kind={kind}')
    titles = [(' '.join(t.split())) for t in (ctx.get('titles') or []) if isinstance(t, str)]
    titles = [t for t in titles if t][:40]
    if titles:
        parts.append('current result titles:\n' + '\n'.join('- ' + t[:120] for t in titles))
    if not parts:
        return message
    return message + '\n\n[context]\n' + '\n'.join(parts)


_chat_seen = set()
_chat_seen_lock = threading.Lock()


def ask_chat(model_id, effort, message):
    name = chat_name(model_id)
    _ensure_agent(model_id, name)
    if effort and effort != 'default':
        _ag(['agents', 'set', name, '--effort', effort])
    with _chat_seen_lock:
        first = name not in _chat_seen
    prompt = (CHAT_PREAMBLE + '\n\n' + message) if first else message
    r = _ag(['chat', 'send', name, prompt, '--json', '--timeout', '240'], timeout=250)
    out = json.loads(r.stdout)
    if out.get('ok') is False:
        raise RuntimeError(str(out)[:200])
    data = out.get('data') or {}
    reply = data.get('reply') if isinstance(data, dict) else None
    if not isinstance(reply, str):
        reply = json.dumps(data)[:4000]
    with _chat_seen_lock:
        _chat_seen.add(name)
    return reply


def run_chat(chat):
    chat['status'] = 'running'
    model = str(chat.get('model') or '')
    message = str(chat.get('message') or '').strip()
    ctx = chat.get('context') if isinstance(chat.get('context'), dict) else {}
    try:
        if not message:
            raise ValueError('empty message')
        reply = ask_chat(model, chat.get('effort') or 'default',
                         chat_prompt(message, ctx))
        text, actions = parse_chat_reply(reply)
        chat['reply'] = text
        chat['actions'] = actions
        chat['status'] = 'done'
    except Exception as e:
        chat['status'] = 'error'
        chat['reply'] = chat.get('reply') or None
        chat['actions'] = chat.get('actions') or None
        chat['error'] = f'{type(e).__name__}: {e}'[:300]
    return chat


def reset_chat(model_id):
    name = chat_name(model_id)
    r = _ag(['agents', 'rm', name])
    with _chat_seen_lock:
        _chat_seen.discard(name)
    return {'ok': True, 'removed': r.returncode == 0}


def run(job):
    job.setdefault('status', 'running')
    query = ' '.join(str(job.get('query') or '').split())
    kind = job.get('kind') or 'images'
    model = job.get('model') or 'none'
    effort = job.get('effort') or 'default'
    linework_only = bool(job.get('linework_only'))
    limit = max(1, min(500, int(job.get('limit') or 200)))
    log(job, f'search {query!r} kind={kind} model={model} effort={effort} '
             f'linework_only={linework_only} limit={limit}')
    try:
        if model != 'none':
            stage(job, 'planning', f'{model_label(model)} is brainstorming search terms…')
        else:
            stage(job, 'planning', 'Turning your request into searches…')
        p = plan(model, effort, query, kind, job)
        stage(job, 'fetching', 'Searching sources…')
        log(job, 'fetching…')
        items = fetch_items(kind, p['queries'], p['icons'], p['commons_categories'], job)
        log(job, f'{len(items)} unique results')
        if (kind == 'match' or linework_only) and items:
            items = score_items(job, items, drop_weak=linework_only)
        if model != 'none' and items:
            items = curate(model, effort, query, items, job)
        stage(job, 'saving', 'Saving results…')
        items = items[:limit]
        os.makedirs(SAVED, exist_ok=True)
        taken = {f[:-5] for f in os.listdir(SAVED) if f.endswith('.json')}
        slug = slugify(query, taken)
        meta = {'slug': slug,
                'title': ' '.join(w[0].upper() + w[1:] for w in query.split(' ')) or slug,
                'query': query, 'kind': kind, 'model': model,
                'created': time.strftime('%Y-%m-%d %H:%M'), 'count': len(items)}
        with open(os.path.join(SAVED, slug + '.json'), 'w') as f:
            json.dump({'meta': meta, 'items': items}, f, indent=1)
        job['slug'] = slug
        job['items'] = items
        job['status'] = 'done'
        stage(job, 'done', f'{len(items)} results ready.')
        log(job, f'saved {slug}.json ({len(items)} items) — done')
    except Exception as e:
        job['status'] = 'error'
        job['error'] = f'{type(e).__name__}: {e}'
        stage(job, 'error', 'Search failed — see details.')
        log(job, f'ERROR {job["error"]}')
    return job
