import urllib.parse

import sources

API = 'https://api.si.edu/openaccess/api/v1.0/search'

SOURCE = {'name': 'smithsonian', 'label': 'Smithsonian Open Access', 'needs_key': 'SI_API_KEY',  # free key: api.data.gov/signup (DEMO_KEY ~30 req/h)
          'about': 'Smithsonian CC0 media: art, specimen, ephemera collections',
          'kinds': ['reference', 'illustration', 'scrapbook'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        n = max(int(limit or 0), 0)
        key = sources.get_key('SI_API_KEY')
        qs = urllib.parse.urlencode({'q': query + ' AND online_media_type:Images',
                                     'rows': n, 'api_key': key})
        d = sources._get_json(API + '?' + qs)
        rows = ((d.get('response') or {}).get('rows') or [])
        out = []
        for r in rows:
            content = r.get('content') if isinstance(r.get('content'), dict) else {}
            dnr = ((content.get('descriptiveNonRepeating') or {})
                   if isinstance(content.get('descriptiveNonRepeating'), dict) else {})
            med = (dnr.get('online_media') or {})
            for m in (med.get('media') or []):
                if not isinstance(m, dict) or str(m.get('type') or '').lower() != 'images':
                    continue
                usage = m.get('usage') or {}
                access = usage.get('access') if isinstance(usage, dict) else usage
                if str(access or '').upper() != 'CC0':
                    continue
                resources = [res for res in (m.get('resources') or [])
                             if isinstance(res, dict) and res.get('url')]
                largest = max(resources, key=lambda res: (
                    sources._int(res.get('width')) * sources._int(res.get('height')),
                    int(any(word in str(res.get('label') or '').lower()
                            for word in ('high-resolution', 'high resolution', 'full resolution', 'original')))
                )) if resources else {}
                image = largest.get('url') or m.get('content')
                w = largest.get('width') or m.get('width')
                h = largest.get('height') or m.get('height')
                it = sources._item(r.get('title'), image,
                                   m.get('thumbnail') or m.get('content') or image,
                                   dnr.get('record_link') or r.get('link') or '',
                                   'Smithsonian', 'CC0', w, h,
                                   query, 'art')
                if it:
                    out.append(it)
                    break
        return out[:n]
    except Exception:
        return []
