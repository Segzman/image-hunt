import urllib.parse

import sources

API = 'https://www.flickr.com/services/rest/'
LICENSES = '4,5,6,7,8,9,10'
LICENSE_NAMES = {'4': 'CC BY', '5': 'CC BY-SA', '6': 'CC BY-ND',
                 '7': 'No known copyright restrictions', '8': 'U.S. Government Work',
                 '9': 'CC0', '10': 'Public Domain Mark'}

SOURCE = {'name': 'flickr', 'label': 'Flickr', 'needs_key': 'FLICKR_API_KEY',
          'about': 'Flickr Creative Commons and public domain photos — needs an API key',
          'kinds': ['reference', 'illustration', 'texture'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        key = sources.get_key('FLICKR_API_KEY')
        if not key:
            return []
        n = max(int(limit or 0), 0)
        qs = urllib.parse.urlencode({'method': 'flickr.photos.search', 'api_key': key,
                                     'text': query, 'format': 'json', 'nojsoncallback': 1,
                                     'per_page': n, 'license': LICENSES,
                                     'extras': 'url_o,url_l,url_c,url_z,url_m,url_s,license,owner_name'})
        d = sources._get_json(API + '?' + qs)
        rows = ((d.get('photos') or {}).get('photo') or [])
        out = []
        for r in rows:
            full = r.get('url_o') or r.get('url_l') or r.get('url_c') or r.get('url_z') or r.get('url_m')
            thumb = r.get('url_m') or r.get('url_s') or r.get('url_q') or full
            owner = r.get('ownername') or ''
            license_id = str(r.get('license') or '')
            page = r.get('url') or ('https://www.flickr.com/photos/{}/{}'.format(
                r.get('owner') or '', r.get('id') or ''))
            it = sources._item(r.get('title') or 'Flickr photo', full, thumb, page,
                               'Flickr · ' + owner if owner else 'Flickr',
                               LICENSE_NAMES.get(license_id, 'CC/PD'),
                               r.get('width_o') or r.get('width_l') or r.get('width_m'),
                               r.get('height_o') or r.get('height_l') or r.get('height_m'),
                               query, 'image')
            if it:
                out.append(it)
        return out[:n]
    except Exception:
        return []
