import concurrent.futures as cf
import urllib.parse

import sources

API = 'https://data.rijksmuseum.nl/search/collection'

SOURCE = {'name': 'rijks', 'label': 'Rijksmuseum', 'needs_key': None,
          'about': 'Rijksmuseum collection objects with open image access',
          'kinds': ['illustration', 'sketch', 'scrapbook', 'reference'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        n = max(int(limit or 0), 0)
        if not n:
            return []
        groups = []
        for field in ('title', 'creator'):
            qs = urllib.parse.urlencode({field: query, 'imageAvailable': 'true'})
            d = sources._get_json(API + '?' + qs)
            groups.append([r for r in (d.get('orderedItems') or [])
                           if isinstance(r, dict)][:n])

        # Search both names because a person's name belongs to the creator field,
        # while ordinary image queries commonly match the title field.
        rows = []
        seen = set()
        for i in range(max(map(len, groups), default=0)):
            for group in groups:
                if i >= len(group):
                    continue
                row = group[i]
                if row.get('id') and row['id'] not in seen:
                    seen.add(row['id'])
                    rows.append(row)
        with cf.ThreadPoolExecutor(6) as ex:
            return [it for it in ex.map(lambda r: _object(r, query), rows) if it][:n]
    except Exception:
        return []


def _as_list(value):
    if isinstance(value, dict):
        return [value]
    return value if isinstance(value, list) else []


def _first_id(value):
    return next((item.get('id') for item in _as_list(value)
                 if isinstance(item, dict) and item.get('id')), '')


def _resolve(uri):
    # Search results use canonical id.rijksmuseum.nl identifiers. The data host
    # serves their Linked Art representation directly when a profile is given.
    uri = uri.replace('https://id.rijksmuseum.nl/', 'https://data.rijksmuseum.nl/')
    sep = '&' if '?' in uri else '?'
    return sources._get_json(uri + sep + '_profile=la-framed')


def _object(row, query):
    try:
        page = row.get('id') or ''
        if not page:
            return None
        record = _resolve(page)
        title = next((name.get('content') for name in _as_list(record.get('identified_by'))
                      if isinstance(name, dict) and name.get('type') == 'Name'
                      and name.get('content')), '')

        # Current Linked Art data links an object to a VisualItem, then to a
        # DigitalObject that carries the actual IIIF access point.
        visual_id = _first_id(record.get('shows'))
        if not visual_id:
            return None
        visual = _resolve(visual_id)
        digital_id = _first_id(visual.get('digitally_shown_by'))
        if not digital_id:
            return None
        digital = _resolve(digital_id)
        image = _first_id(digital.get('access_point'))
        if not image:
            return None

        rights = next((r.get('id') for r in _as_list(record.get('subject_to'))
                       if isinstance(r, dict) and r.get('id')), 'see page')
        return sources._item(title, image, image, page, 'Rijksmuseum', rights,
                             0, 0, query, 'art')
    except Exception:
        return None
