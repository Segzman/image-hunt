import urllib.parse

import sources

API = 'https://api.artic.edu/api/v1/artworks/search'
IIIF = 'https://www.artic.edu/iiif/2/{id}/full/{px},/0/default.jpg'

SOURCE = {'name': 'aic', 'label': 'Art Institute of Chicago', 'needs_key': None,
          'about': 'public-domain Chicago museum art: etchings, prints, drawings',
          'kinds': ['illustration', 'sketch', 'scrapbook', 'reference'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        n = max(int(limit or 0), 0)
        qs = urllib.parse.urlencode({'q': query,
                                     'query[term][is_public_domain]': 'true',
                                     'fields': 'id,title,image_id,artist_display,thumbnail',
                                     'limit': n})
        d = sources._get_json(API + '?' + qs)
        out = []
        for r in (d.get('data') or []):
            iid = r.get('image_id')
            if not iid:
                continue
            th = r.get('thumbnail') if isinstance(r.get('thumbnail'), dict) else {}
            it = sources._item(r.get('title') or r.get('artist_display'),
                               IIIF.format(id=iid, px=1686), IIIF.format(id=iid, px=400),
                               f'https://www.artic.edu/artworks/{r.get("id")}',
                               'AIC', 'CC0', th.get('width'), th.get('height'),
                               query, 'art')
            if it:
                out.append(it)
        return out[:n]
    except Exception:
        return []
