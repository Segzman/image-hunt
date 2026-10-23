SOURCE = {'name': 'iconify', 'label': 'Iconify', 'needs_key': None,
          'about': 'Iconify vector icon search: SVG line art at 1024px',
          'kinds': ['lineart'],
          'search': lambda query, limit: search(query, limit)}


def search(query, limit):
    try:
        import hunt
        out = hunt.iconify_search(query, query, limit=max(int(limit or 0), 0) or 1)
        return list(out)[:max(int(limit or 0), 0)]
    except Exception:
        return []
