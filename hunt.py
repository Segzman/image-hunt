import io
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import concurrent.futures as cf

try:
    import sources
except Exception:
    sources = None

ROOT = os.path.dirname(os.path.abspath(__file__))
SAVED = os.path.join(ROOT, 'saved')
CUSTOM_DIR = os.path.join(ROOT, 'sources', 'custom')
KEYS_PATH = os.path.join(os.path.expanduser('~'), '.config', 'image-hunt', 'keys.json')
AGENT_DIR = os.path.join(ROOT, '.agent')
if os.name == 'nt':
    HELPER_SANDBOX = os.path.join(os.environ.get('LOCALAPPDATA') or
                                  os.path.expanduser('~/AppData/Local'),
                                  'Image Hunt', 'helper-sandbox')
else:
    HELPER_SANDBOX = os.path.expanduser('~/.cache/image-hunt/helper-sandbox')


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

MODES = {
    'anything': {'label': 'Anything', 'about': 'whatever fits the idea: photos, memes, art, objects',
                 'sources': 'all', 'linework': False, 'min_w': 600},
    'reference': {'label': 'Photo reference', 'about': 'clear, well-lit photographs to draw from',
                  'sources': ['unsplash', 'pexels', 'nasa', 'openverse', 'commons', 'loc', 'smithsonian'],
                  'linework': False, 'min_w': 600},
    'illustration': {'label': 'Illustration & art', 'about': 'illustrations, prints and colourful artwork',
                     'sources': ['aic', 'met', 'cleveland', 'smithsonian', 'openverse', 'commons', 'pixabay'],
                     'linework': False, 'min_w': 600},
    'sketch': {'label': 'Sketch & etching', 'about': 'drawings, sketches and etchings, ink linework emphasised',
               'sources': ['aic', 'met', 'cleveland', 'loc', 'openverse', 'commons'],
               'linework': True, 'min_w': 600},
    'lineart': {'label': 'Line art SVG', 'about': 'clean line art: monochrome SVG icons and colourful SVG illustrations',
                'sources': ['iconify', 'pixabay'], 'linework': False, 'min_w': 600},
    'texture': {'label': 'Texture & pattern', 'about': 'textures, patterns and paper backgrounds',
                'sources': ['unsplash', 'pexels', 'pixabay', 'nasa', 'openverse'],
                'linework': False, 'min_w': 600},
    'scrapbook': {'label': 'Scrapbook & ephemera',
                  'about': 'vintage ephemera: labels, stamps, tickets, botanical and scientific plates',
                  'sources': ['aic', 'met', 'cleveland', 'loc', 'smithsonian', 'commons', 'pixabay'],
                  'linework': False, 'min_w': 600},
    'match': {'label': 'Style match (woodcut-ish)',
              'about': 'woodcut / engraving style vintage ink illustrations',
              'sources': ['commons', 'openverse', 'aic', 'met', 'cleveland', 'loc'],
              'linework': True, 'min_w': 600},
}
LEGACY_MODES = {'images': 'anything'}
SOURCE_LIMIT = 40
SOURCE_TIMEOUT = 20
SOURCE_CACHE_TTL = 1800
_SOURCE_CACHE = {}
_SOURCE_CACHE_LOCK = threading.Lock()

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
    head, sep, last = thumb.rpartition('/')
    return head + sep + re.sub(r'\d+px-', f'{px}px-', last, count=1)


_WIN_RESERVED = {'CON', 'PRN', 'AUX', 'NUL'} | {
    f'{p}{n}' for p in ('COM', 'LPT') for n in '123456789¹²³'}


def safe_name(name):
    name = (name or '').strip()
    name = re.sub(r'[/\\:*?"<>|]+', '_', name)
    name = re.sub(r'\s+', ' ', name)
    name = re.sub(r'[\x00-\x1f\x7f]', '', name).strip('. ')
    if not name:
        name = 'image'
    name = name[:120]
    # Linux/macOS cap names at 255 UTF-8 bytes
    while len(name.encode('utf-8')) > 255:
        name = name[:-1]
    name = name.strip('. ') or 'image'
    if name.split('.')[0].rstrip(' ').upper() in _WIN_RESERVED:
        name = '_' + name
    return name


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


def _get_json(url, timeout, max_bytes=None, extra_headers=None):
    headers = dict(UA)
    headers.update(extra_headers or {})
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(max_bytes + 1) if max_bytes else r.read()
    if max_bytes and len(raw) > max_bytes:
        raise ValueError('response too large')
    return json.loads(raw.decode('utf-8', 'replace').strip() or 'null')


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


def canonical_mode(mode):
    mode = str(mode or 'anything')
    mode = LEGACY_MODES.get(mode, mode)
    return mode if mode in MODES else 'anything'


def _entry_field(entry, key, default=None):
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, key, default)


def _fallback_sources():
    # Used only for names the sources package does not provide (including the merge stand-in).
    all_modes = list(MODES)
    return {
        'openverse': {'name': 'openverse', 'label': 'Openverse', 'kinds': all_modes,
                      'needs_key': None, 'about': 'Openverse CC search',
                      'search': lambda q, limit: openverse(q, q)[:limit]},
        'commons': {'name': 'commons', 'label': 'Wikimedia Commons', 'kinds': all_modes,
                    'needs_key': None, 'about': 'Wikimedia Commons',
                    'search': lambda q, limit: commons_search(q, q)[:limit]},
        'iconify': {'name': 'iconify', 'label': 'Iconify', 'kinds': ['lineart'],
                    'needs_key': None, 'about': 'Iconify SVG icon sets',
                    'search': lambda q, limit: iconify_search(q, q, min(int(limit), 96))[:limit]},
    }


def dotted_get(obj, path):
    path = str(path or '').strip()
    if path.startswith('$.'):
        path = path[2:]
    if not path:
        return obj
    cur = obj
    for part in path.split('.'):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def _dotted_ok(path):
    return isinstance(path, str) and bool(
        re.fullmatch(r'[A-Za-z0-9_$-]+(?:\.[A-Za-z0-9_$-]+)*', path.strip()))


def source_spec_error(spec):
    if not isinstance(spec, dict):
        return 'spec must be an object'
    name = spec.get('name')
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,49}', name):
        return 'name must be lowercase letters, digits, - and _'
    if not isinstance(spec.get('label'), str) or not spec['label'].strip():
        return 'label required'
    kinds = spec.get('kinds')
    if not isinstance(kinds, list) or not kinds:
        return 'kinds must be a non-empty list'
    if any(not isinstance(k, str) or k not in MODES for k in kinds):
        return 'kinds must be mode ids'
    if not isinstance(spec.get('about'), str) or not spec['about'].strip():
        return 'about required'
    url = spec.get('url')
    if not isinstance(url, str) or not url.startswith('https://'):
        return 'url must be https'
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return 'url is not valid'
    if parsed.scheme != 'https' or not parsed.netloc:
        return 'url must be https'
    if '{query}' not in url:
        return 'url must contain {query}'
    if any(p not in ('query', 'limit', 'key') for p in re.findall(r'\{(\w+)\}', url)):
        return 'unknown url placeholder'
    if not _dotted_ok(spec.get('items')):
        return 'items must be a dotted path'
    fields = spec.get('fields')
    if not isinstance(fields, dict) or not fields:
        return 'fields required'
    for key in ('url', 'thumb'):
        if not _dotted_ok(fields.get(key)):
            return f'fields.{key} must be a dotted path'
    if any(not _dotted_ok(v) for k, v in fields.items() if k not in ('url', 'thumb')):
        return 'field paths must be dotted paths'
    key_env = spec.get('key_env')
    if key_env is not None and (not isinstance(key_env, str)
                                or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key_env)):
        return 'key_env must be an ENV_NAME'
    return None


def validate_source_spec(spec):
    if source_spec_error(spec):
        return None
    out = dict(spec)
    out['name'] = spec['name'].strip()
    out['label'] = spec['label'].strip()
    out['about'] = spec['about'].strip()
    out['url'] = spec['url'].strip()
    out['items'] = spec['items'].strip()
    out['kinds'] = list(dict.fromkeys(k.strip() for k in spec['kinds']))
    out['fields'] = {k: v.strip() for k, v in spec['fields'].items()}
    if spec.get('key_env'):
        out['key_env'] = spec['key_env'].strip()
    else:
        out.pop('key_env', None)
    return out


def load_keys():
    try:
        with open(KEYS_PATH) as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int, float))}


def source_key(env_name):
    if not env_name:
        return None
    return os.environ.get(env_name) or load_keys().get(env_name) or None


def save_keys(values):
    keys = load_keys()
    saved = []
    for name, value in (values or {}).items():
        name = str(name)
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', name):
            raise ValueError(f'bad key name: {name}')
        if not isinstance(value, str):
            raise ValueError(f'key {name} must be a string')
        value = value.strip()
        saved.append(name)
        if value:
            keys[name] = value
        else:
            keys.pop(name, None)
    os.makedirs(os.path.dirname(KEYS_PATH), exist_ok=True)
    fd = os.open(KEYS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(keys, f, indent=1)
    try:
        os.chmod(KEYS_PATH, 0o600)
    except OSError:
        pass
    return saved


def custom_source_path(name):
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,49}', name):
        return None
    return os.path.join(CUSTOM_DIR, name + '.json')


def load_custom_sources():
    try:
        names = sorted(f[:-5] for f in os.listdir(CUSTOM_DIR) if f.endswith('.json'))
    except OSError:
        return []
    out = []
    for name in names:
        try:
            with open(os.path.join(CUSTOM_DIR, name + '.json')) as f:
                spec = validate_source_spec(json.load(f))
        except Exception:
            spec = None
        if spec:
            out.append(spec)
    return out


def custom_source_entry(spec):
    return {'name': spec['name'], 'label': spec['label'], 'kinds': spec['kinds'],
            'needs_key': spec.get('key_env'), 'about': spec['about'], 'custom': True,
            'search': lambda q, limit: custom_search(spec, q, limit)}


def save_custom_source(spec):
    spec = validate_source_spec(spec)
    if not spec:
        raise ValueError(source_spec_error(spec) or 'invalid source spec')
    path = custom_source_path(spec['name'])
    if not path:
        raise ValueError('bad source name')
    os.makedirs(CUSTOM_DIR, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(spec, f, indent=1)
    return spec


def delete_custom_source(name):
    path = custom_source_path(name)
    if not path or not os.path.isfile(path):
        return False
    os.remove(path)
    return True


def custom_search(spec, query, limit=20):
    spec = validate_source_spec(spec)
    if not spec:
        return []
    try:
        limit = max(1, int(limit or 20))
    except (TypeError, ValueError):
        limit = 20
    try:
        url = spec['url'].replace('{query}', urllib.parse.quote_plus(str(query or '')))
        url = url.replace('{limit}', str(limit))
        headers = {}
        key = source_key(spec.get('key_env'))
        if key:
            url = url.replace('{key}', urllib.parse.quote_plus(key))
            headers = {'Authorization': 'Bearer ' + key, 'X-Api-Key': key}
        data = _get_json(url, 20, max_bytes=5 * 1024 * 1024,
                         extra_headers=headers) if headers else \
            _get_json(url, 20, max_bytes=5 * 1024 * 1024)
    except Exception:
        return []
    rows = dotted_get(data, spec['items'])
    if not isinstance(rows, list):
        return []
    fields = spec['fields']
    out = []
    for row in rows:
        def val(key):
            return dotted_get(row, fields[key]) if key in fields else None
        url = val('url')
        thumb = val('thumb')
        if not isinstance(url, str) or not url.startswith('https://') or not thumb:
            continue
        title = val('title')
        page = val('page')
        lic = val('lic')
        w = val('w')
        h = val('h')
        try:
            w = int(w) if w is not None else 0
        except (TypeError, ValueError):
            w = 0
        try:
            h = int(h) if h is not None else 0
        except (TypeError, ValueError):
            h = 0
        out.append({'title': str(title or spec['label'])[:300], 'url': url,
                    'thumb': str(thumb), 'page': str(page or url), 'src': spec['label'],
                    'lic': str(lic or ''), 'w': w, 'h': h, 'tag': str(query or ''),
                    'kind': 'art' if str(url).lower().endswith('.svg') else 'image'})
        if len(out) >= limit:
            break
    return out


def _source_catalog():
    cat = _fallback_sources()
    registry = getattr(sources, 'REGISTRY', None) if sources else None
    if isinstance(registry, dict):
        for name, entry in registry.items():
            if not name or not (isinstance(entry, dict) or hasattr(entry, 'search')):
                continue
            if isinstance(entry, dict):
                e = dict(entry)
            else:
                e = {k: getattr(entry, k, None)
                     for k in ('name', 'label', 'kinds', 'needs_key', 'about', 'search')}
            e.setdefault('name', name)
            e.setdefault('label', e['name'])
            e.setdefault('kinds', list(MODES))
            e.setdefault('needs_key', None)
            e.setdefault('about', '')
            cat[e['name']] = e
    for spec in load_custom_sources():
        entry = custom_source_entry(spec)
        cat[entry['name']] = entry
    return cat


def _available_source_names(cat=None):
    cat = _source_catalog() if cat is None else cat
    avail = set()
    if sources and hasattr(sources, 'available'):
        try:
            listed = sources.available()
        except Exception:
            listed = []
        if isinstance(listed, dict):
            avail.update(str(k) for k in listed)
        else:
            for entry in listed or []:
                if isinstance(entry, str):
                    avail.add(entry)
                elif isinstance(entry, dict) and entry.get('name'):
                    avail.add(entry['name'])
                elif getattr(entry, 'name', None):
                    avail.add(entry.name)
    out = set()
    for name, entry in cat.items():
        needs = _entry_field(entry, 'needs_key')
        if not needs or name in avail or source_key(needs):
            out.add(name)
    return out


def mode_source_names(mode, enabled=None, preferred=None):
    mode = canonical_mode(mode)
    cat = _source_catalog()
    avail = _available_source_names(cat)
    spec = MODES[mode]
    custom = {name for name, entry in cat.items()
              if _entry_field(entry, 'custom') and mode in (_entry_field(entry, 'kinds') or [])}
    if enabled is not None:
        names = [n for n in enabled if isinstance(n, str) and n in cat and n in avail]
    elif spec['sources'] == 'all':
        names = [n for n in cat if n in avail]
    else:
        names = [n for n in spec['sources'] if n in cat and n in avail]
        names.extend(n for n in custom if n in avail and n not in names)
    if preferred:
        wanted = {n for n in preferred if isinstance(n, str) and n in cat}
        names = [n for n in names if n in wanted] or names
    return names


def sources_info():
    cat = _source_catalog()
    avail = _available_source_names(cat)
    out = []
    for name, entry in cat.items():
        kinds = _entry_field(entry, 'kinds') or []
        out.append({'name': name, 'label': _entry_field(entry, 'label') or name,
                    'kinds': list(kinds), 'needs_key': _entry_field(entry, 'needs_key'),
                    'about': _entry_field(entry, 'about') or '',
                    'available': name in avail,
                    'custom': bool(_entry_field(entry, 'custom'))})
    out.sort(key=lambda s: (s['custom'], s['name']))
    modes = [{'id': k, 'label': v['label'], 'about': v['about'],
              'linework': v['linework'], 'sources': v['sources']} for k, v in MODES.items()]
    return {'sources': out, 'modes': modes}


def _run_source(entry, query, limit):
    fn = _entry_field(entry, 'search')
    if not callable(fn):
        return []
    items = fn(query, limit) or []
    label = _entry_field(entry, 'label') or _entry_field(entry, 'name') or ''
    out = []
    for item in items:
        if not isinstance(item, dict) or not item.get('url'):
            continue
        item = dict(item)
        item.setdefault('title', '')
        item.setdefault('thumb', item['url'])
        item.setdefault('page', item['url'])
        item.setdefault('src', label)
        item.setdefault('lic', '')
        item.setdefault('tag', query)
        item.setdefault('kind', 'image')
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _cached_items(name, query, limit, fetch):
    key = (name, str(query), int(limit))
    now = time.monotonic()
    with _SOURCE_CACHE_LOCK:
        cached = _SOURCE_CACHE.get(key)
        if cached and now - cached[0] < SOURCE_CACHE_TTL:
            return [dict(i) for i in cached[1]]
    items = fetch()
    with _SOURCE_CACHE_LOCK:
        _SOURCE_CACHE[key] = (time.monotonic(), [dict(i) for i in items])
        now = time.monotonic()
        for old in [k for k, v in _SOURCE_CACHE.items() if now - v[0] >= SOURCE_CACHE_TTL]:
            _SOURCE_CACHE.pop(old, None)
    return items


def _cached_source(entry, query, limit):
    name = _entry_field(entry, 'name') or ''
    return _cached_items(name, query, limit, lambda: _run_source(entry, query, limit))


def _tokens(text):
    return set(re.findall(r'\w+', str(text or '').lower()))


def _item_dimensions(item):
    try:
        w, h = int(item.get('w') or 0), int(item.get('h') or 0)
    except (TypeError, ValueError):
        return 0, 0
    return max(0, w), max(0, h)


def _safe_float(value):
    try:
        n = float(value or 0)
        return n if math.isfinite(n) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _mode_bonus(item, mode):
    kind = item.get('kind') or 'image'
    if mode in ('sketch', 'lineart', 'match'):
        try:
            return min(1.0, max(0.0, float(item.get('line') or (1 if kind == 'icon' else 0))))
        except (TypeError, ValueError):
            return 1.0 if kind == 'icon' else 0.0
    w, h = _item_dimensions(item)
    if mode == 'texture':
        return min(w, h) / max(w, h) if w and h else 0.0
    if mode == 'reference':
        return 1.0 if kind == 'image' and not str(item.get('url') or '').lower().split('?')[0].endswith('.svg') else 0.0
    return 0.0


def _rank_score(item, query, plan_terms, mode):
    title = _tokens(item.get('title'))
    groups = [_tokens(term) for term in [query, *(plan_terms or [])]]
    groups = [g for g in groups if g]
    relevance = max((len(g & title) / len(g) for g in groups), default=0.0)
    w, h = _item_dimensions(item)
    resolution = min(1.0, math.log1p(w * h) / math.log1p(3000 * 3000)) if w and h else 0.0
    return round(relevance * .5 + resolution * .3 + _mode_bonus(item, mode) * .2, 3)


def rank_items(items, query='', plan_terms=None, mode='anything'):
    mode = canonical_mode(mode)
    for item in items:
        item['score'] = _rank_score(item, query, plan_terms, mode)
    return sorted(items, key=lambda i: -(i.get('score') or 0))


def _normal_title(title):
    return ' '.join(re.findall(r'\w+', str(title or '').lower()))


def _url_basename(url):
    path = urllib.parse.unquote(urllib.parse.urlsplit(str(url or '')).path).lower()
    # IIIF (AIC, V&A, …): every image ends in /full/<size>/0/default.jpg — the identifier is the path before /full/
    if '/iiif/' in path or '/full/' in path:
        return path.split('/full/')[0]
    return re.sub(r'\.[^.]+$', '', os.path.basename(path))


def dedupe_items(items):
    seen_titles, seen_basenames, out = set(), set(), []
    for item in sorted(items, key=lambda i: -_safe_float(i.get('score'))):
        title = _normal_title(item.get('title'))
        src = str(item.get('src') or '').strip().lower()
        basename = _url_basename(item.get('url'))
        title_key = (src, title) if title else None
        if (title_key and title_key in seen_titles) or (basename and basename in seen_basenames):
            continue
        if title_key:
            seen_titles.add(title_key)
        if basename:
            seen_basenames.add(basename)
        out.append(item)
    return out


def _licence_matches(item, licence):
    text = str(item.get('lic') or '').lower()
    words = set(re.findall(r'[a-z0-9]+', text))
    if licence == 'pd':
        return 'public domain' in text or 'pd' in words or 'cc0' in words
    if licence == 'cc':
        return ('cc' in words or 'creative commons' in text) and 'cc0' not in words
    return True


def _kind_matches(item, kind):
    if kind == 'any':
        return True
    is_svg = item.get('kind') in ('icon', 'art') or \
        str(item.get('url') or '').lower().split('?', 1)[0].split('#', 1)[0].endswith('.svg')
    return is_svg if kind == 'svg' else not is_svg


def filter_items(items, filters=None, favorites=None):
    filters = filters or {}
    source = filters.get('source') or ''
    try:
        min_size = max(0, int(filters.get('min_size') or 0))
    except (TypeError, ValueError):
        min_size = 0
    orientation = filters.get('orientation') or 'any'
    licence = filters.get('license') or 'any'
    kind = filters.get('kind') or 'any'
    text = str(filters.get('text') or '').strip().lower()
    fav_only = bool(filters.get('favorites_only'))
    favourites = set(favorites or ())
    out = []
    for item in items:
        if source and item.get('src') != source:
            continue
        w, h = _item_dimensions(item)
        # Sources that cannot report image pixels should not be filtered out
        # just because their dimensions are unknown.
        if min_size and w and h and min(w, h) < min_size:
            continue
        if orientation == 'portrait' and not (w and h and h > w):
            continue
        if orientation == 'landscape' and not (w and h and w > h):
            continue
        if orientation == 'square' and not (w and h and abs(w - h) / max(w, h) <= .1):
            continue
        if licence not in ('any', '') and not _licence_matches(item, licence):
            continue
        if not _kind_matches(item, kind):
            continue
        if text and text not in ' '.join(str(item.get(k) or '') for k in ('title', 'src', 'tag')).lower():
            continue
        if fav_only and item.get('url') not in favourites:
            continue
        out.append(item)
    sort = filters.get('sort') or 'best'
    if sort == 'largest':
        out.sort(key=lambda i: _item_dimensions(i)[0] * _item_dimensions(i)[1], reverse=True)
    elif sort == 'source':
        out.sort(key=lambda i: str(i.get('src') or '').casefold())
    else:
        out.sort(key=lambda i: _safe_float(i.get('score')), reverse=True)
    return out


def _prepare_items(items, query, plan_terms, mode):
    unique = []
    min_w = int(MODES[mode].get('min_w') or 0)
    for item in items:
        url = item.get('url')
        if not url:
            continue
        if item.get('kind') in ('icon', 'art') or str(url).lower().endswith('.svg'):
            unique.append(item)
        elif not item.get('w') or _item_dimensions(item)[0] >= min_w:
            unique.append(item)
    return dedupe_items(rank_items(unique, query, plan_terms, mode))


def fetch_items(mode, queries, icons, categories, job, enabled=None, preferred=None):
    mode = canonical_mode(mode)
    cat = _source_catalog()
    names = mode_source_names(mode, enabled=enabled, preferred=preferred)
    tasks = []
    for name in names:
        entry = cat[name]
        for q in queries:
            tasks.append((name, q, lambda e=entry, t=q: _cached_source(e, t, SOURCE_LIMIT)))
        if mode == 'lineart' and name == 'iconify':
            for n in icons:
                tasks.append((name, n, lambda e=entry, t=n: _cached_source(e, t, 96)))
    if mode == 'match' and categories and 'commons' in names:
        for c in categories:
            tasks.append(('commons-cat', c,
                          lambda t=c: _cached_items('commons-cat', t, 0,
                                                    lambda: commons_categories([t], t))))
    if not names:
        log(job, 'no sources available for this mode — pick another mode or add a key')

    all_items = []
    total = len(tasks)
    if total:
        stage(job, 'fetching', f'Searching sources (0/{total})…', [0, total])
    job['items'] = []
    job['partial'] = bool(total)
    by_src, done = {}, 0
    pool = cf.ThreadPoolExecutor(12)
    futs = {pool.submit(fn): (label, query) for label, query, fn in tasks}
    pending = set(futs)
    deadline = time.monotonic() + SOURCE_TIMEOUT
    terms = list(queries)
    original_query = job.get('query') or (terms[0] if terms else '')
    while pending:
        remaining = max(0, deadline - time.monotonic())
        finished, pending = cf.wait(pending, timeout=remaining,
                                    return_when=cf.FIRST_COMPLETED)
        if not finished:
            break
        for fut in finished:
            label, query = futs[fut]
            done += 1
            try:
                result = fut.result()
                by_src.setdefault(label, []).extend(result)
                all_items.extend(result)
                job['items'] = _prepare_items(all_items, original_query, terms, mode)
            except Exception as e:
                log(job, f'source {label} skipped: {type(e).__name__}: {str(e)[:120]}')
            stage(job, 'fetching', f'Searching sources ({done}/{total})…', [done, total])
    for fut in pending:
        label, _ = futs[fut]
        fut.cancel()
        log(job, f'source {label} timed out after {SOURCE_TIMEOUT}s')
        done += 1
        stage(job, 'fetching', f'Searching sources ({done}/{total})…', [done, total])
    pool.shutdown(wait=False, cancel_futures=True)
    for label, items in by_src.items():
        log(job, f'{label}: {len(items)} hits')
    kept = _prepare_items(all_items, original_query, terms, mode)
    job['items'] = kept
    job['partial'] = False
    dropped = len(all_items) - len(kept)
    min_w = int(MODES[mode].get('min_w') or 0)
    if dropped:
        log(job, f'{dropped} duplicates or too small (width < {min_w}) dropped')
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
    os.makedirs(HELPER_SANDBOX, exist_ok=True)
    listing = json.loads(_ag(['agents', 'list', '--json']).stdout)
    if isinstance(listing, dict) and isinstance(listing.get('data'), dict):
        listing = listing['data']
    agents = listing if isinstance(listing, list) else (listing.get('agents') or [])
    existing = next((a for a in agents if isinstance(a, dict) and a.get('name') == name), None)
    if existing:
        old_dir = existing.get('dir')
        old_sandbox = os.path.join(ROOT, '.helper-sandbox')
        if old_dir and os.path.normcase(os.path.abspath(old_dir)) == \
                os.path.normcase(os.path.abspath(old_sandbox)):
            r = _ag(['agents', 'set', name, '--dir', HELPER_SANDBOX])
            if r.returncode != 0:
                raise RuntimeError(f'agents set dir failed: {(r.stderr or r.stdout).strip()[:160]}')
        return False
    backend, model = model_backend(model_id)
    if not (backend and model):
        raise RuntimeError(f'unknown model: {model_id!r}')
    r = _ag(['agents', 'add', name, '--backend', backend, '--model', model,
             '--data', 'public', '--mode', 'ro', '--dir', HELPER_SANDBOX])
    if r.returncode != 0:
        raise RuntimeError(f'agents add failed: {(r.stderr or r.stdout).strip()[:160]}')
    return True


def _reset_helper(name, job):
    try:
        r = _ag(['agents', 'rm', name])
        if r.returncode != 0:
            log(job, f'could not remove timed-out helper {name}')
    except Exception as e:
        log(job, f'could not remove timed-out helper {name}: {type(e).__name__}')
    meta = os.path.join(AGENT_DIR, 'chats', name + '.meta.json')
    try:
        os.remove(meta)
    except FileNotFoundError:
        pass
    except OSError as e:
        log(job, f'could not clear timed-out helper session: {type(e).__name__}')


def ask_helper(model_id, effort, prompt, job):
    if model_id == 'none' or not model_id:
        return ''
    name = helper_name(model_id)
    timeout = 90 if job.get('stage') == 'planning' else 120
    log(job, f'asking AI helper {name}…')
    try:
        if _ensure_agent(model_id, name):
            log(job, f'helper {name} created')
        models = discover_models().get('models') or []
        model_info = next((m for m in models if m.get('id') == model_id), None)
        if model_info is None:
            model_info = next((m for m in MODELS if m.get('id') == model_id), {})
        backend = model_info.get('backend') or model_backend(model_id)[0]
        available_efforts = model_info.get('efforts')
        helper_effort = effort if effort and effort != 'default' else (
            'low' if backend == 'opencode' and isinstance(available_efforts, list)
            and 'low' in available_efforts else '')
        r = _ag(['agents', 'set', name, '--effort', helper_effort])
        if r.returncode != 0:
            raise RuntimeError(f'agents set effort failed: {(r.stderr or r.stdout).strip()[:160]}')
        try:
            r = _ag(['chat', 'send', name, prompt, '--json', '--timeout', str(timeout)],
                    timeout=timeout + 10)
        except subprocess.TimeoutExpired:
            log(job, f'AI helper timed out after {timeout}s — continuing without AI')
            _reset_helper(name, job)
            return ''
        out = json.loads(r.stdout)
        if out.get('ok') is False:
            raise RuntimeError(str(out)[:200])
        data = out.get('data') or {}
        reply = data.get('reply') if isinstance(data, dict) else None
        return reply if isinstance(reply, str) else json.dumps(data)[:4000]
    except Exception as e:
        log(job, f'helper failed ({type(e).__name__}: {str(e)[:120]}) — continuing without AI')
        return ''


def plan(model_id, effort, query, mode, job):
    mode = canonical_mode(mode)
    fallback = {'queries': [query],
                'icons': [w for w in query.split() if len(w) > 2][:4],
                'commons_categories': [], 'sources': []}
    if model_id == 'none' or not model_id:
        log(job, f'queries: {fallback["queries"]}')
        return fallback
    cat = _source_catalog()
    names = [n for n in mode_source_names(mode) if n in cat]
    source_lines = '\n'.join(f'- {n}: {_entry_field(cat[n], "about") or n}' for n in names[:30])
    prompt = (
        f'You help an artist find images: {MODES[mode]["about"]}. Search phrase: "{query}".\n'
        f'Craft better search terms. Reply with ONLY JSON, no prose or markdown fences, shaped exactly:\n'
        f'{{"queries": [6-10 search phrases],\n'
        f' "icons": [single noun words for icon search],\n'
        f' "commons_categories": [0-5 Wikimedia Commons category names without the "Category:" prefix],\n'
        f' "sources": [up to 6 source names from the list below that fit best]}}\n'
        f'Keep "icons" empty unless the mode is lineart. Keep entries short and in English.\n'
        f'Available sources:\n{source_lines}'
    )
    reply = ask_helper(model_id, effort, prompt, job)
    d = extract_json(reply)
    if not isinstance(d, dict):
        log(job, 'plan fallback (no usable JSON)')
        log(job, f'queries: {fallback["queries"]}')
        return fallback
    plan_d = {'queries': fallback['queries'], 'icons': [], 'commons_categories': [], 'sources': []}
    for key, cap in (('queries', 10), ('icons', 4), ('commons_categories', 5)):
        vals = d.get(key)
        if isinstance(vals, list):
            plan_d[key] = [str(v).strip()[:120] for v in vals
                           if isinstance(v, str) and v.strip()][:cap]
    if not plan_d['queries']:
        plan_d['queries'] = fallback['queries']
    if mode != 'lineart':
        plan_d['icons'] = []
    valid = set(mode_source_names(mode))
    vals = d.get('sources')
    if isinstance(vals, list):
        plan_d['sources'] = [str(v).strip() for v in vals
                             if isinstance(v, str) and str(v).strip() in valid][:6]
    log(job, f'queries: {plan_d["queries"]} · icons: {plan_d["icons"]} · '
             f'categories: {plan_d["commons_categories"]} · sources: {plan_d["sources"]}')
    return plan_d


def curate(model_id, effort, query, items, job):
    if model_id == 'none' or not model_id or not items:
        return items
    stage(job, 'curating', f'{model_label(model_id)} is picking the best ones…')
    ranked = sorted(items, key=lambda i: -_safe_float(i.get('score')))
    candidates = ranked[:60]
    lines = []
    for i, it in enumerate(candidates):
        title = re.sub(r'\s+', ' ', str(it.get('title') or ''))[:150]
        w, h = _item_dimensions(it)
        line = it.get('line')
        lines.append(f'{i} | {title} | {it.get("src") or ""} | {w or "?"}×{h or "?"} | line={line if line is not None else "?"}')
    prompt = (
        f'User searched for: "{query}". Judging which search results are genuinely relevant.\n'
        f'Reorder and flag results, but keep every result (never remove items). Reply with ONLY JSON: '
        f'{{"picks": [{{"i": <index>, "why": "<=8 words"}}]}} — '
        f'pick only items a human would want; 0 to 60 picks.\n\n' + '\n'.join(lines)
    )
    reply = ask_helper(model_id, effort, prompt, job)
    d = extract_json(reply)
    picks = d.get('picks') if isinstance(d, dict) and isinstance(d.get('picks'), list) else []
    picked = []
    seen = set()
    for p in picks:
        try:
            idx = int(p.get('i'))
            why = ' '.join(str(p.get('why', '')).split())[:60]
        except Exception:
            continue
        if 0 <= idx < len(candidates) and idx not in seen:
            item = candidates[idx]
            item['pick'] = True
            item['why'] = why
            picked.append(item)
            seen.add(idx)
    if picked:
        picked_ids = {id(i) for i in picked}
        items = picked + [i for i in ranked if id(i) not in picked_ids]
        log(job, f'AI picked {len(picked)} items')
    else:
        items = ranked
        log(job, 'AI picked nothing (kept all)')
    return items


CHAT_PREAMBLE = (
    'You help an artist find images for illustration, design, scrapbooking and drawing '
    'reference. Be concrete: suggest specific subjects, eras, artists, styles and '
    'public-domain-friendly sources.\n'
    'When you propose searches, END your reply with a fenced code block labelled '
    '"searches" holding a JSON array of 1-5 objects shaped exactly:\n'
    '[{"query": "search phrase", "kind": "anything|reference|illustration|sketch|lineart|texture|scrapbook|match", '
    '"linework_only": false, "why": "short reason"}]\n'
    'Example:\n'
    '```searches\n'
    '[{"query":"botanical plate fern","kind":"scrapbook","linework_only":false,"why":"vintage scientific plates"}]\n'
    '```\n'
    'Never put the searches fence more than once. Reply text stays plain prose outside the block.\n'
    'If a new web API would materially help, you may additionally END with ONE fenced block '
    'labelled "source" holding a declarative custom-source JSON shaped exactly:\n'
    '{"name":"lower-id","label":"Human label","kinds":["illustration"],"about":"one line",'
    '"url":"https://api.example.com/search?q={query}&limit={limit}","items":"results",'
    '"fields":{"url":"url","thumb":"thumb","title":"title","page":"page","lic":"license"}}\n'
    'The url must be https and GET-only; items is a dotted path to the result list.')


def chat_preamble():
    text = CHAT_PREAMBLE
    cat = _source_catalog()
    lines = [f'- {n}: {_entry_field(cat[n], "about") or _entry_field(cat[n], "label") or n}'
             for n in sorted(_available_source_names(cat)) if n in cat]
    if lines:
        text += '\n\nAvailable image sources:\n' + '\n'.join(lines[:30])
    return text


def parse_chat_reply(text):
    reply = (text or '').strip()
    actions = []
    source_actions = []
    for m in list(re.finditer(r'```source[ \t]*\r?\n(.*?)\r?\n?```', reply, re.S))[:3]:
        parsed = extract_json(m.group(1))
        if isinstance(parsed, dict):
            spec = validate_source_spec(parsed)
            err = source_spec_error(parsed)
            source_actions.append({'type': 'source', 'spec': parsed,
                                   'label': str(parsed.get('label') or parsed.get('name') or ''),
                                   'error': err if not spec else None})
    reply = re.sub(r'```source[ \t]*\r?\n.*?\r?\n?```', '', reply, flags=re.S).strip()
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
                ok = kind in MODES or kind in ('images', 'lineart', 'match')
                actions.append({'query': ' '.join(q.split())[:120],
                                'kind': kind if ok else 'images',
                                'linework_only': bool(a.get('linework_only')),
                                'why': ' '.join(str(a.get('why') or '').split())[:80]})
                if len(actions) >= 5:
                    break
        reply = (reply[:m.start()] + reply[m.end():]).strip()
    return reply, actions + source_actions


def prepare_source_actions(actions, ctx):
    ctx = ctx if isinstance(ctx, dict) else {}
    query = str(ctx.get('query') or '').strip()
    if not query:
        search = next((a.get('query') for a in actions if a.get('type') != 'source'), '')
        query = str(search or 'art')
    out = []
    for action in actions:
        if action.get('type') != 'source':
            out.append(action)
            continue
        spec = validate_source_spec(action.get('spec'))
        if not spec:
            continue
        hits = [h for h in custom_search(spec, query, 4) if h.get('url') and h.get('thumb')]
        if not hits:
            continue
        action = dict(action)
        action.update({'spec': spec, 'label': spec['label'], 'query': query,
                       'hits': hits[:4], 'error': None})
        out.append(action)
    return out


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
    prompt = (chat_preamble() + '\n\n' + message) if first else message
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
        chat['actions'] = prepare_source_actions(actions, ctx)
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
    mode = canonical_mode(job.get('mode') or job.get('kind'))
    job['mode'] = job['kind'] = mode
    model = job.get('model') or 'none'
    effort = job.get('effort') or 'default'
    linework_only = bool(job.get('linework_only'))
    selected = job.get('sources') if isinstance(job.get('sources'), list) else None
    limit = max(1, min(500, int(job.get('limit') or 200)))
    log(job, f'search {query!r} mode={mode} model={model} effort={effort} '
             f'linework_only={linework_only} limit={limit}')
    try:
        if model != 'none':
            stage(job, 'planning', f'{model_label(model)} is brainstorming search terms…')
        else:
            stage(job, 'planning', 'Turning your request into searches…')
        p = plan(model, effort, query, mode, job)
        stage(job, 'fetching', 'Searching sources…')
        log(job, 'fetching…')
        items = fetch_items(mode, p['queries'], p['icons'], p['commons_categories'], job,
                            enabled=selected, preferred=p.get('sources'))
        log(job, f'{len(items)} unique results')
        if (MODES[mode]['linework'] or linework_only) and items:
            items = score_items(job, items, drop_weak=linework_only)
            items = dedupe_items(rank_items(items, query, p['queries'], mode))
        if model != 'none' and items:
            items = curate(model, effort, query, items, job)
        stage(job, 'saving', 'Saving results…')
        items = items[:limit]
        os.makedirs(SAVED, exist_ok=True)
        taken = {f[:-5] for f in os.listdir(SAVED) if f.endswith('.json')}
        slug = slugify(query, taken)
        meta = {'slug': slug,
                'title': ' '.join(w[0].upper() + w[1:] for w in query.split(' ')) or slug,
                'query': query, 'kind': mode, 'model': model,
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
