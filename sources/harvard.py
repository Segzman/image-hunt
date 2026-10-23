import urllib.parse

import sources

API = 'https://api.harvardartmuseums.org/object'

SOURCE = {'name': 'harvard', 'label': 'Harvard Art Museums', 'needs_key': 'HARVARD_API_KEY',
          'about': 'Harvard Art Museums collection images — needs an API key',
          'kinds': ['illustration', 'sketch', 'scrapbook', 'reference'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        key = sources.get_key('HARVARD_API_KEY')
        if not key:
            return []
        n = max(int(limit or 0), 0)
        qs = urllib.parse.urlencode({'apikey': key, 'q': query, 'hasimage': 1, 'size': n})
        d = sources._get_json(API + '?' + qs)
        out = []
        for r in (d.get('records') or []):
            if not isinstance(r, dict):
                continue
            base = r.get('baseimageurl') or ''
            image = base.rstrip('/') + '/full/full/0/default.jpg' if base else r.get('primaryimageurl')
            thumb = r.get('primaryimageurl') or (base.rstrip('/') + '/full/400,/0/default.jpg'
                                                  if base else '')
            it = sources._item(r.get('title'), image, thumb, r.get('url') or '',
                               'Harvard Art Museums', r.get('copyright') or r.get('rights') or 'see page',
                               r.get('width'), r.get('height'), query, 'art')
            if it:
                out.append(it)
        return out[:n]
    except Exception:
        return []
