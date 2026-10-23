import urllib.parse

import sources

API = 'https://archive.org/advancedsearch.php'
META = 'https://archive.org/metadata/'
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.tif', '.tiff', '.jp2', '.bmp', '.webp')

SOURCE = {'name': 'archive', 'label': 'Internet Archive', 'needs_key': None,
          'about': 'Internet Archive image files and illustrated ephemera',
          'kinds': ['illustration', 'sketch', 'scrapbook', 'reference', 'texture'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        n = max(int(limit or 0), 0)
        qs = urllib.parse.urlencode({'q': query + ' AND mediatype:image',
                                     'fl[]': ['identifier', 'title'],
                                     'rows': n, 'output': 'json'}, doseq=True)
        d = sources._get_json(API + '?' + qs)
        rows = (((d.get('response') or {}).get('docs')) or [])
        out = []
        for row in rows:
            if not isinstance(row, dict) or not row.get('identifier'):
                continue
            it = _item(row, query)
            if it:
                out.append(it)
        return out[:n]
    except Exception:
        return []


def _item(row, query):
    try:
        ident = str(row['identifier'])
        d = sources._get_json(META + urllib.parse.quote(ident, safe=''))
        files = [f for f in (d.get('files') or []) if isinstance(f, dict)]
        images = [f for f in files
                  if str(f.get('name') or '').lower().endswith(IMAGE_EXTS)
                  and 'thumb' not in str(f.get('name') or '').lower()]
        if not images:
            return None
        image = max(images, key=lambda f: sources._int(f.get('size')))
        name = str(image.get('name') or '')
        if not name:
            return None
        metadata = d.get('metadata') if isinstance(d.get('metadata'), dict) else {}
        lic = metadata.get('licenseurl') or metadata.get('license') or 'see page'
        encoded = urllib.parse.quote(name, safe='/')
        base = 'https://archive.org/download/' + urllib.parse.quote(ident, safe='') + '/'
        return sources._item(row.get('title'), base + encoded,
                             base + '__ia_thumb.jpg',
                             'https://archive.org/details/' + urllib.parse.quote(ident, safe=''),
                             'Internet Archive', lic, 0, 0, query, 'image')
    except Exception:
        return None
