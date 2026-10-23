import urllib.parse

import sources

API = 'https://api.europeana.eu/record/v2/search.json'

SOURCE = {'name': 'europeana', 'label': 'Europeana', 'needs_key': 'EUROPEANA_API_KEY',
          'about': 'Europeana open images from cultural heritage collections — needs an API key',
          'kinds': ['illustration', 'sketch', 'scrapbook', 'reference'],
          'search': lambda query, limit: search(query, limit)}


def _first(value):
    return value[0] if isinstance(value, list) and value else value


def search(query, limit):
    try:
        key = sources.get_key('EUROPEANA_API_KEY')
        if not key:
            return []
        n = max(int(limit or 0), 0)
        qs = urllib.parse.urlencode({'query': query, 'media': 'true',
                                     'thumbnail': 'true', 'reusability': 'open',
                                     'rows': n})
        d = sources._get_json(API + '?' + qs, headers={'X-Api-Key': key})
        out = []
        for r in (d.get('items') or []):
            if not isinstance(r, dict):
                continue
            image = _first(r.get('edmIsShownBy') or r.get('edmHasView'))
            thumb = _first(r.get('edmPreview'))
            if isinstance(image, dict):
                image = image.get('id') or image.get('def')
            if isinstance(thumb, dict):
                thumb = thumb.get('id') or thumb.get('def')
            page = _first(r.get('edmIsShownAt')) or r.get('guid') or ''
            rights = _first(r.get('edmRights') or r.get('rights')) or 'see page'
            if isinstance(rights, dict):
                rights = rights.get('def') or rights.get('id') or 'see page'
            title = _first(r.get('title') or r.get('dcTitle'))
            it = sources._item(title, image, thumb, page,
                               _first(r.get('dataProvider')) or 'Europeana',
                               rights, 0, 0, query, 'art')
            if it:
                out.append(it)
        return out[:n]
    except Exception:
        return []
