import urllib.parse

import sources

API = 'https://pixabay.com/api/'

SOURCE = {'name': 'pixabay', 'label': 'Pixabay', 'needs_key': 'PIXABAY_API_KEY',
          'about': 'Pixabay illustration/vector/photo mix — needs an API key',
          'kinds': ['illustration', 'lineart', 'texture', 'reference'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    return _search(query, limit, 'illustration')


def search_vectors(query, limit):
    return _search(query, limit, 'vector')


def _search(query, limit, image_type):
    try:
        key = sources.get_key('PIXABAY_API_KEY')
        if not key:
            return []
        n = max(int(limit or 0), 0)
        qs = urllib.parse.urlencode({'key': key, 'q': query, 'image_type': image_type,
                                     'per_page': n, 'safesearch': 'true'})
        d = sources._get_json(API + '?' + qs)
        out = []
        for r in (d.get('hits') or []):
            out.append(sources._item(r.get('tags') or 'Pixabay image',
                                     r.get('largeImageURL'), r.get('webformatURL'),
                                     r.get('pageURL'), 'Pixabay', 'Pixabay Content License',
                                     r.get('width'), r.get('height'), query, 'image'))
        return out[:n]
    except Exception:
        return []
