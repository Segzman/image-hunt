SOURCE = {'name': 'openverse', 'label': 'Openverse', 'needs_key': None,
          'about': 'Openverse CC search across galleries & media (SVG variant below)',
          'kinds': ['illustration', 'sketch', 'scrapbook', 'reference', 'texture'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    return _run(query, limit, False)


def search_svg(query, limit):
    return _run(query, limit, True)


def _run(query, limit, svg):
    try:
        import hunt
        out = hunt.openverse(query, query, svg=svg)
        return list(out)[:max(int(limit or 0), 0)]
    except Exception:
        return []
