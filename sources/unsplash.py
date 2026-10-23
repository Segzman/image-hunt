import urllib.parse

import sources

API = 'https://api.unsplash.com/search/photos'

SOURCE = {'name': 'unsplash', 'label': 'Unsplash', 'needs_key': 'UNSPLASH_ACCESS_KEY',
          'about': 'Unsplash free photography — needs an access key',
          'kinds': ['reference', 'texture'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        key = sources.get_key('UNSPLASH_ACCESS_KEY')
        if not key:
            return []
        n = max(int(limit or 0), 0)
        qs = urllib.parse.urlencode({'query': query, 'per_page': n})
        d = sources._get_json(API + '?' + qs,
                              headers={'Authorization': 'Client-ID ' + key})
        out = []
        for r in (d.get('results') or []):
            urls = r.get('urls') if isinstance(r.get('urls'), dict) else {}
            out.append(sources._item((r.get('description') or r.get('alt_description')
                                      or '').strip() or 'Unsplash photo',
                                     urls.get('full'), urls.get('small'),
                                     ((r.get('links') or {}).get('html') or ''),
                                     'Unsplash', 'Unsplash License',
                                     r.get('width'), r.get('height'), query, 'image'))
        return out[:n]
    except Exception:
        return []
