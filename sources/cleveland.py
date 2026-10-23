import urllib.parse

import sources

API = 'https://openaccess-api.clevelandart.org/api/artworks/'

SOURCE = {'name': 'cleveland', 'label': 'Cleveland Museum', 'needs_key': None,
          'about': 'CC0 Cleveland Museum art: prints, drawings, posters',
          'kinds': ['illustration', 'sketch', 'scrapbook'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        n = max(int(limit or 0), 0)
        qs = urllib.parse.urlencode({'q': query, 'has_image': '1', 'cc0': '1',
                                     'limit': n})
        d = sources._get_json(API + '?' + qs)
        out = []
        for r in ((d.get('data') or [])):
            ims = r.get('images') if isinstance(r.get('images'), dict) else {}
            pr, web = ims.get('print') or {}, ims.get('web') or {}
            url = pr.get('url') or web.get('url')
            thumb = web.get('url') or pr.get('url')
            it = sources._item(r.get('title') or r.get('accession_number'), url, thumb,
                               r.get('url'), 'Cleveland', 'CC0',
                               (pr if url == pr.get('url') else web).get('width'),
                               (pr if url == pr.get('url') else web).get('height'),
                               query, 'art')
            if it:
                out.append(it)
        return out[:n]
    except Exception:
        return []
