SOURCE = {'name': 'commons', 'label': 'Wikimedia Commons', 'needs_key': None,
          'about': 'Commons search: PD images, engravings, plates, ephemera',
          'kinds': ['illustration', 'sketch', 'scrapbook', 'reference'],
          'search': lambda query, limit: search(query, limit)}

CATEGORY_KINDS = ['scrapbook', 'illustration', 'reference']
CATEGORY_ABOUT = 'Commons by category name: botanical plates, ephemera sets'


def search(query, limit):
    return _run(query, limit, False)


def search_bitmap(query, limit):
    return _run(query, limit, True)


def search_categories(query, limit):
    try:
        import hunt
        out = hunt.commons_categories([query], query)
        return list(out)[:max(int(limit or 0), 0)]
    except Exception:
        return []


def _run(query, limit, bitmap):
    try:
        import hunt
        out = hunt.commons_search(query, query, bitmap=bitmap)
        return list(out)[:max(int(limit or 0), 0)]
    except Exception:
        return []
