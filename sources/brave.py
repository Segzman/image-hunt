import urllib.parse

import sources

API = 'https://api.search.brave.com/res/v1/images/search'

SOURCE = {'name': 'brave', 'label': 'Brave Image Search', 'needs_key': 'BRAVE_API_KEY',
          'about': 'Brave web image search — needs an API key',
          'kinds': ['reference', 'illustration', 'sketch', 'texture', 'scrapbook'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        key = sources.get_key('BRAVE_API_KEY')
        if not key:
            return []
        n = max(int(limit or 0), 0)
        qs = urllib.parse.urlencode({'q': query, 'count': n})
        d = sources._get_json(API + '?' + qs,
                              headers={'X-Subscription-Token': key})
        out = []
        for r in (d.get('results') or []):
            props = r.get('properties') if isinstance(r.get('properties'), dict) else {}
            thumb = r.get('thumbnail') if isinstance(r.get('thumbnail'), dict) else {}
            it = sources._item(r.get('title'), props.get('url'), thumb.get('src'),
                               r.get('url'), 'Brave Search', 'see page',
                               props.get('width'), props.get('height'), query, 'image')
            if it:
                out.append(it)
        return out[:n]
    except Exception:
        return []
