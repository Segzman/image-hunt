import concurrent.futures as cf
import urllib.parse

import sources

API = 'https://collectionapi.metmuseum.org/public/collection/v1'
SEARCH_API = 'https://collectionapi.metmuseum.org/public/collection/v1.1/search'

SOURCE = {'name': 'met', 'label': 'The Met', 'needs_key': None,
          'about': 'public-domain Met collection: paintings, prints, decorative art',
          'kinds': ['illustration', 'sketch', 'scrapbook'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        n = max(int(limit or 0), 0)
        d = sources._get_json(SEARCH_API + '?' + urllib.parse.urlencode(
            {'hasImages': 'true', 'q': query, 'offset': 0, 'limit': n}))
        ids = [i for i in (d.get('objectIDs') or []) if isinstance(i, int)][:n]
        with cf.ThreadPoolExecutor(6) as ex:
            out = [it for it in ex.map(lambda i: _object(i, query), ids) if it]
        return out[:n]
    except Exception:
        return []


def _object(oid, query):
    try:
        o = sources._get_json(f'{API}/objects/{oid}')
        if not o.get('isPublicDomain'):
            return None
        it = sources._item(o.get('title'), o.get('primaryImage'),
                           o.get('primaryImageSmall'),
                           o.get('objectURL') or f'https://www.metmuseum.org/art/collection/search/{oid}',
                           'Met', 'CC0', None, None, query, 'art')
        # Met's Width and Height measurements describe the physical object,
        # not the pixel dimensions of its primary image.
        if it:
            it['w'] = it['h'] = None
        return it
    except Exception:
        return None
