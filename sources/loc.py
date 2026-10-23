import re
import urllib.parse

import sources

API = 'https://www.loc.gov/photos/'

SOURCE = {'name': 'loc', 'label': 'Library of Congress', 'needs_key': None,
          'about': 'LOC photos & prints: historical photos, ephemera, posters',
          'disabled': 'Cloudflare blocks API requests with a 403 challenge',
          'kinds': ['reference', 'scrapbook'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        n = max(int(limit or 0), 0)
        d = sources._get_json(API + '?' + urllib.parse.urlencode(
            {'q': query, 'fo': 'json', 'c': n}))
        out = []
        for r in (d.get('results') or []):
            sizes = _sizes(r.get('image_url') or [])
            if not sizes:
                continue
            big, small = sizes[-1], sizes[0]
            it = sources._item((r.get('title') or '').strip(), big[1], small[1],
                               r.get('id') or r.get('url'), 'LOC',
                               (r.get('rights') or '').strip() or 'see page',
                               big[0], big[2], query, 'image')
            if it:
                out.append(it)
        return out[:n]
    except Exception:
        return []


def _sizes(entries):
    sizes = []
    for e in entries:
        if isinstance(e, (list, tuple)) and len(e) >= 2:
            label, url = str(e[0]), e[1]
        else:
            label, url = '', e
        m = re.match(r'(\d+)x(\d+)?', label)
        sizes.append((int(m.group(1)) if m else 0, url, int(m.group(2)) if m and m.group(2) else 0))
    return [s for s in sorted(sizes, key=lambda s: s[0] or 0) if s[1]]
