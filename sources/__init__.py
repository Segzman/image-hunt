import json
import os
import urllib.request
from dataclasses import dataclass

UA = {'User-Agent': 'image-hunt/1.0 (+https://github.com/Segzman/image-hunt)'}
TIMEOUT = 20
KEYS_FILE = os.path.expanduser('~/.config/image-hunt/keys.json')


@dataclass
class Source:
    name: str
    label: str
    kinds: list
    needs_key: str
    about: str
    search: object
    disabled: str = None


def _get_json(url, timeout=TIMEOUT, headers=None):
    req = urllib.request.Request(url, headers=dict(UA, **(headers or {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace').strip() or 'null')


def _int(v):
    try:
        return int(v or 0)
    except Exception:
        return 0


def _item(title, url, thumb, page, src, lic, w, h, tag, kind):
    if not url or not thumb:
        return None
    return {'title': title or '', 'url': url, 'thumb': thumb, 'page': page or '',
            'src': src, 'lic': lic or 'see page', 'w': _int(w), 'h': _int(h),
            'tag': tag, 'kind': kind}


def read_keys(path=None):
    try:
        with open(path or KEYS_FILE, encoding='utf-8') as f:
            return (json.load(f) or {})
    except Exception:
        return {}


def get_key(env):
    v = os.environ.get(env)
    return v or (read_keys().get(env) or None)


def available():
    return [s for s in REGISTRY.values()
            if not s.disabled and (not s.needs_key or get_key(s.needs_key))]


from . import (aic, met, cleveland, loc, nasa, smithsonian, unsplash, pexels, pixabay,
               openverse, commons, iconify, vam, rijks, archive, europeana, harvard,
               flickr, brave)  # noqa: E402

def _src(d):
    return Source(d['name'], d.get('label') or d['name'], d.get('kinds') or [],
                  d.get('needs_key'), d.get('about') or d['name'], d.get('search'),
                  d.get('disabled'))


REGISTRY = {s.name: s for s in (
    _src(aic.SOURCE), _src(met.SOURCE), _src(cleveland.SOURCE), _src(loc.SOURCE),
    _src(nasa.SOURCE), _src(smithsonian.SOURCE), _src(unsplash.SOURCE),
    _src(pexels.SOURCE), _src(pixabay.SOURCE), _src(openverse.SOURCE),
    _src(commons.SOURCE), _src(vam.SOURCE), _src(rijks.SOURCE), _src(archive.SOURCE),
    _src(europeana.SOURCE), _src(harvard.SOURCE), _src(flickr.SOURCE), _src(brave.SOURCE),
    Source('commons_category', 'Commons by category', commons.CATEGORY_KINDS, None,
           commons.CATEGORY_ABOUT, commons.search_categories),
    _src(iconify.SOURCE),
)}
