import urllib.parse

import sources

API = 'https://images-api.nasa.gov/search'

SOURCE = {'name': 'nasa', 'label': 'NASA Image Library', 'needs_key': None,
          'about': 'NASA space photography: planets, missions, experiments',
          'kinds': ['reference', 'texture'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        n = max(int(limit or 0), 0)
        qs = urllib.parse.urlencode({'q': query, 'media_type': 'image'})
        d = sources._get_json(API + '?' + qs)
        col = ((d.get('collection') or {}).get('items') or [])
        out = []
        for it in col:
            data = next((x for x in (it.get('data') or []) if isinstance(x, dict)), {})
            links = next((x for x in (it.get('links') or []) if isinstance(x, dict)), {})
            thumb = (links or {}).get('href') or ''
            if not thumb:
                continue
            full = thumb
            for size in ('~thumb', '~small', '~medium', '~large'):
                if size in full:
                    full = full.replace(size, '~orig')
                    break
            out.append(sources._item(data.get('title'),
                                     full, thumb,
                                     it.get('href'), 'NASA',
                                     data.get('copyright') or 'NASA public domain',
                                     0, 0, query, 'image'))
        return out[:n]
    except Exception:
        return []
