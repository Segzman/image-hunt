import urllib.parse

import sources

API = 'https://api.pexels.com/v1/search'

SOURCE = {'name': 'pexels', 'label': 'Pexels', 'needs_key': 'PEXELS_API_KEY',
          'about': 'Pexels free photography — needs an API key',
          'kinds': ['reference', 'texture'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        key = sources.get_key('PEXELS_API_KEY')
        if not key:
            return []
        n = max(int(limit or 0), 0)
        qs = urllib.parse.urlencode({'query': query, 'per_page': n})
        d = sources._get_json(API + '?' + qs, headers={'Authorization': key})
        out = []
        for r in (d.get('photos') or []):
            src = r.get('src') if isinstance(r.get('src'), dict) else {}
            who = r.get('photographer') or ''
            out.append(sources._item(
                (str(r.get('alt') or '') + (f' — {who}' if who else '')).strip(' —'),
                src.get('original'), src.get('medium'), r.get('url'),
                'Pexels · ' + who if who else 'Pexels', 'Pexels License',
                r.get('width'), r.get('height'), query, 'image'))
        return out[:n]
    except Exception:
        return []
