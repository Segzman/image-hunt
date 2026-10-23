import urllib.parse

import sources

API = 'https://api.vam.ac.uk/v2/objects/search'

SOURCE = {'name': 'vam', 'label': 'V&A', 'needs_key': None,
          'about': 'V&A collection objects with IIIF images',
          'kinds': ['illustration', 'sketch', 'scrapbook', 'reference'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        n = max(int(limit or 0), 0)
        qs = urllib.parse.urlencode({'q': query, 'images_exist': 1, 'page_size': n})
        d = sources._get_json(API + '?' + qs)
        out = []
        for r in (d.get('records') or []):
            images = r.get('_images') if isinstance(r.get('_images'), dict) else {}
            base = images.get('_iiif_image_base_url') or ''
            if not base:
                continue
            base = base.rstrip('/')
            titles = r.get('titles') or []
            title = (titles[0].get('title') if titles and isinstance(titles[0], dict)
                     else r.get('title'))
            thumb = images.get('_primary_thumbnail') or base + '/full/400,/0/default.jpg'
            page = r.get('objectURL') or ''
            if not page and r.get('systemNumber'):
                page = 'https://collections.vam.ac.uk/item/' + str(r['systemNumber'])
            it = sources._item(title, base + '/full/1600,/0/default.jpg', thumb,
                               page, 'V&A', 'see page', 0, 0, query, 'art')
            if it:
                out.append(it)
        return out[:n]
    except Exception:
        return []
