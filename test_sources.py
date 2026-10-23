import json
import os
import tempfile
import urllib.parse
import unittest
from unittest import mock

import hunt

import sources

REST_SOURCES = ('aic', 'met', 'cleveland', 'loc', 'nasa', 'smithsonian', 'unsplash',
                'pexels', 'pixabay', 'vam', 'rijks', 'archive', 'europeana',
                'harvard', 'flickr', 'brave')
ALL_NAMES = set(REST_SOURCES) | {'openverse', 'commons', 'commons_category', 'iconify'}


class FixtureTest(unittest.TestCase):
    """Offline: every search parses a small captured-shape JSON via a fake fetch."""

    def route(self, table):
        calls = []

        def fake_get_json(url, timeout=sources.TIMEOUT, headers=None):
            calls.append((url, headers))
            for prefix, payload in table.items():
                if url.startswith(prefix):
                    if isinstance(payload, Exception):
                        raise payload
                    return payload
            raise ValueError('no fixture for ' + url[:120])

        patcher = mock.patch.object(sources, '_get_json', fake_get_json)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def no_keys(self, *keep_env):
        """Blank env + missing keys file, so key checks are deterministic."""
        p = mock.patch.dict(os.environ, {}, clear=True)
        p.start()
        self.addCleanup(p.stop)
        tmp = tempfile.mkdtemp()
        patcher = mock.patch.object(sources, 'KEYS_FILE', os.path.join(tmp, 'keys.json'))
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in keep_env:
            os.environ[k] = 'test-key'

    def no_keys_route(self, table, *keep_env):
        self.no_keys(*keep_env)
        return self.route(table)


class RegistryTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(set(sources.REGISTRY), ALL_NAMES)

    def test_source_shape(self):
        for s in sources.REGISTRY.values():
            self.assertIsInstance(s, sources.Source)
            self.assertTrue(isinstance(s.label, str) and s.label)
            self.assertTrue(isinstance(s.kinds, list) and
                            all(isinstance(k, str) and k for k in s.kinds))
            self.assertTrue(s.needs_key is None or isinstance(s.needs_key, str))
            self.assertTrue(isinstance(s.about, str) and s.about)
            self.assertTrue(callable(s.search))

    def test_loc_is_disabled_with_reason(self):
        loc = sources.REGISTRY['loc']
        self.assertTrue(isinstance(loc.disabled, str) and loc.disabled)

    def test_needs_keys(self):
        keyed = {s.name: s.needs_key for s in sources.REGISTRY.values() if s.needs_key}
        self.assertEqual(keyed, {'smithsonian': 'SI_API_KEY', 'unsplash': 'UNSPLASH_ACCESS_KEY',
                                 'pexels': 'PEXELS_API_KEY',
                                 'pixabay': 'PIXABAY_API_KEY',
                                 'europeana': 'EUROPEANA_API_KEY',
                                 'harvard': 'HARVARD_API_KEY',
                                 'flickr': 'FLICKR_API_KEY',
                                 'brave': 'BRAVE_API_KEY'})

    def test_available_returns_sources(self):
        self.assertTrue(all(isinstance(s, sources.Source)
                            for s in sources.available()))


class AvailableTest(FixtureTest):
    def test_keyless_only_without_keys(self):
        self.no_keys()
        names = {s.name for s in sources.available()}
        self.assertEqual(names, ALL_NAMES - {'smithsonian', 'unsplash', 'pexels', 'pixabay', 'loc',
                                             'europeana', 'harvard', 'flickr', 'brave'})

    def test_env_key_includes_source(self):
        self.no_keys('PEXELS_API_KEY')
        names = {s.name for s in sources.available()}
        self.assertIn('pexels', names)
        self.assertEqual(sources.get_key('PEXELS_API_KEY'), 'test-key')

    def test_keys_file_includes_source(self):
        self.no_keys()
        with open(sources.KEYS_FILE, 'w') as f:
            json.dump({'PIXABAY_API_KEY': 'file-key'}, f)
        names = {s.name for s in sources.available()}
        self.assertIn('pixabay', names)
        self.assertEqual(sources.get_key('PIXABAY_API_KEY'), 'file-key')

    def test_env_wins_over_file(self):
        self.no_keys()
        with open(sources.KEYS_FILE, 'w') as f:
            json.dump({'UNSPLASH_ACCESS_KEY': 'file-key'}, f)
        with mock.patch.dict(os.environ, {'UNSPLASH_ACCESS_KEY': 'env-key'}):
            self.assertEqual(sources.get_key('UNSPLASH_ACCESS_KEY'), 'env-key')

    def test_malformed_keys_file_is_absent(self):
        self.no_keys()
        with open(sources.KEYS_FILE, 'w') as f:
            f.write('{nope')
        self.assertEqual(sources.read_keys(), {})
        self.assertIsNone(sources.get_key('UNSPLASH_ACCESS_KEY'))
        self.assertEqual({s.name for s in sources.available()},
                         ALL_NAMES - {'smithsonian', 'unsplash', 'pexels', 'pixabay', 'loc',
                                      'europeana', 'harvard', 'flickr', 'brave'})


class AicTest(FixtureTest):
    def test_items(self):
        calls = self.route({'https://api.artic.edu': {
            'data': [{'id': 7, 'title': 'Bare Mountain', 'image_id': 'a1b2',
                      'artist_display': 'unsigned', 'thumbnail': {'width': 400, 'height': 300}},
                     {'id': 8, 'title': 'no image', 'image_id': None}]}})
        [it] = sources.aic.search('mountain etchings', 10)
        self.assertEqual(it['url'], 'https://www.artic.edu/iiif/2/a1b2/full/1686,/0/default.jpg')
        self.assertEqual(it['thumb'], 'https://www.artic.edu/iiif/2/a1b2/full/400,/0/default.jpg')
        self.assertEqual(it['page'], 'https://www.artic.edu/artworks/7')
        self.assertEqual(it['src'], 'AIC')
        self.assertEqual(it['lic'], 'CC0')
        self.assertEqual(it['kind'], 'art')
        self.assertEqual((it['w'], it['h']), (400, 300))
        self.assertEqual(it['tag'], 'mountain etchings')
        self.assertIn('query%5Bterm%5D%5Bis_public_domain%5D=true', calls[0][0])
        self.assertIn('limit=10', calls[0][0])

    def test_limit(self):
        self.route({'https://api.artic.edu': {'data': [
            {'id': i, 'title': f'a{i}', 'image_id': f'x{i}'} for i in range(9)]}})
        self.assertEqual([i['title'] for i in sources.aic.search('q', 3)], ['a0', 'a1', 'a2'])


class MetTest(FixtureTest):
    ROUTES = {'https://collectionapi.metmuseum.org/public/collection/v1.1/search?': {
                  'objectIDs': [1, 2, 3], 'total': 3},
              'https://collectionapi.metmuseum.org/public/collection/v1/objects/1': {
                  'objectID': 1, 'isPublicDomain': True, 'title': 'Bridge',
                  'primaryImage': 'https://met.m/1.jpg',
                  'primaryImageSmall': 'https://met.m/1s.jpg',
                  'objectURL': 'https://www.metmuseum.org/art/collection/search/1',
                  'measurements': [{'elementMeasurements': {'Width': 620, 'Height': 800}}]},
              'https://collectionapi.metmuseum.org/public/collection/v1/objects/2': {
                  'objectID': 2, 'isPublicDomain': True, 'title': 'Second',
                  'primaryImage': 'https://met.m/2.jpg',
                  'primaryImageSmall': 'https://met.m/2s.jpg'},
              'https://collectionapi.metmuseum.org/public/collection/v1/objects/3': {
                  'objectID': 3, 'isPublicDomain': False,
                  'primaryImage': '3.jpg'}}

    def test_items(self):
        calls = self.route(MetTest.ROUTES)
        [it] = sources.met.search('bridges', 1)
        self.assertEqual(it['title'], 'Bridge')
        self.assertEqual(it['url'], 'https://met.m/1.jpg')
        self.assertEqual(it['thumb'], 'https://met.m/1s.jpg')
        self.assertEqual(it['page'], 'https://www.metmuseum.org/art/collection/search/1')
        # Met dimensions describe the physical object, not its image pixels.
        self.assertEqual((it['w'], it['h']), (None, None))
        self.assertEqual(hunt.filter_items([it], {'min_size': 1200}), [it])
        self.assertEqual(it['src'], 'Met')
        self.assertEqual(it['lic'], 'CC0')
        self.assertEqual(it['kind'], 'art')
        self.assertEqual(len(calls), 2)  # 1-object search + both fetched (limit 1)
        self.assertIn('/v1.1/search?', calls[0][0])
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(calls[0][0]).query)
        self.assertEqual(q['offset'], ['0'])
        self.assertEqual(q['limit'], ['1'])

    def test_limit(self):
        self.route(MetTest.ROUTES)
        out = sources.met.search('q', 2)
        self.assertEqual(len(out), 2)
        self.assertEqual([i['title'] for i in out], ['Bridge', 'Second'])


class ClevelandTest(FixtureTest):
    def test_items(self):
        calls = self.route({'https://openaccess-api.clevelandart.org': {'data': [
            {'title': 'The Mower', 'url': 'https://cma.org/art/1',
             'images': {'print': {'url': 'https://cma.org/p.jpg', 'width': 3300,
                                  'height': 2400},
                        'web': {'url': 'https://cma.org/w.jpg'}}},
            {'title': 'naked', 'url': 'https://cma.org/art/2', 'images': {}}]}})
        [it] = sources.cleveland.search('mower', 10)
        self.assertEqual(it['url'], 'https://cma.org/p.jpg')
        self.assertEqual(it['thumb'], 'https://cma.org/w.jpg')
        self.assertEqual((it['w'], it['h']), (3300, 2400))
        self.assertEqual(it['page'], 'https://cma.org/art/1')
        self.assertEqual(it['src'], 'Cleveland')
        self.assertEqual(it['lic'], 'CC0')
        self.assertIn('has_image=1', calls[0][0])
        self.assertIn('cc0=1', calls[0][0])


class LocTest(FixtureTest):
    def test_items(self):
        calls = self.route({'https://www.loc.gov/photos/?': {'results': [
            {'title': 'Ballyhooly, Co. Cork', 'id': 'https://www.loc.gov/item/i922/',
             'image_url': [['144x108', 'small.jpg'], ['640x480', 'big.jpg']]},
            {'title': 'No urls', 'id': 'x', 'image_url': []}]}})
        [it] = sources.loc.search('ballyhooly', 10)
        self.assertEqual(it['url'], 'big.jpg')
        self.assertEqual(it['thumb'], 'small.jpg')
        self.assertEqual((it['w'], it['h']), (640, 480))
        self.assertEqual(it['page'], 'https://www.loc.gov/item/i922/')
        self.assertEqual(it['src'], 'LOC')
        self.assertEqual(it['kind'], 'image')
        self.assertIn('fo=json', calls[0][0])
        self.assertIn('c=10', calls[0][0])


class NasaTest(FixtureTest):
    def test_items(self):
        calls = self.route({'https://images-api.nasa.gov/search?': {'collection': {'items': [
            {'href': 'https://i.nasa/asset/col.json', 'data': [{'title': 'Earthrise'}],
             'links': [{'href': 'https://i.nasa/img~thumb.jpg'}]},
            {'href': 'https://i.nasa/asset/x.json', 'data': [{'title': 'no link'}]}]}}})
        [it] = sources.nasa.search('earthrise', 10)
        self.assertEqual(it['url'], 'https://i.nasa/img~orig.jpg')
        self.assertEqual(it['thumb'], 'https://i.nasa/img~thumb.jpg')
        self.assertEqual(it['title'], 'Earthrise')
        self.assertEqual(it['page'], 'https://i.nasa/asset/col.json')
        self.assertEqual(it['src'], 'NASA')
        self.assertEqual(it['kind'], 'image')
        self.assertIn('media_type=image', calls[0][0])

    def test_small_variant_uses_orig_for_full(self):
        self.route({'https://images-api.nasa.gov/search?': {'collection': {'items': [
            {'href': 'https://i.nasa/asset/col.json', 'data': [{'title': 'Moon'}],
             'links': [{'href': 'https://i.nasa/img~small.jpg'}]}]}}})
        [it] = sources.nasa.search('moon', 10)
        self.assertEqual(it['thumb'], 'https://i.nasa/img~small.jpg')
        self.assertEqual(it['url'], 'https://i.nasa/img~orig.jpg')


class SmithsonianTest(FixtureTest):
    ROWS = {'response': {'rows': [
        {'title': 'Plum Blossom', 'id': 'edanmdm-nmah_1440738',
         'content': {'descriptiveNonRepeating': {
             'record_link': 'https://www.si.edu/object/nmah_1440738',
             'online_media': {'media': [
                 {'type': 'Images', 'content': 'si_fallback', 'thumbnail': 'si_th',
                  'idsId': 'nmah_1440738', 'usage': {'access': 'CC0'},
                  'resources': [
                      {'label': 'Screen Image', 'url': 'si_screen',
                       'width': 800, 'height': 560},
                      {'label': 'High-resolution JPEG', 'url': 'si_full',
                       'width': 4000, 'height': 2800}]},
                 {'type': 'PDF', 'content': 'skip', 'usage': {'access': 'CC0'}}]}}}},
        {'title': 'Restricted', 'id': 'edanmdm-x',
         'content': {'descriptiveNonRepeating': {
             'online_media': {'media': [
                 {'type': 'Images', 'content': 'no', 'thumbnail': 't',
                  'usage': {'access': 'CC BY-NC'}}]}}}}]}}

    def test_parse_with_key(self):  # DEMO_KEY fallback dropped: ~30 req/h, exhausted in normal use
        calls = self.no_keys_route({'https://api.si.edu': SmithsonianTest.ROWS}, 'SI_API_KEY')
        out = sources.smithsonian.search('plum', 10)
        self.assertEqual(len(out), 1)
        it = out[0]
        self.assertEqual(it['url'], 'si_full')
        self.assertEqual(it['thumb'], 'si_th')
        self.assertEqual(it['page'], 'https://www.si.edu/object/nmah_1440738')
        self.assertEqual(it['src'], 'Smithsonian')
        self.assertEqual(it['lic'], 'CC0')
        self.assertEqual((it['w'], it['h']), (4000, 2800))
        self.assertIn('api_key=test-key', calls[0][0])
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(calls[0][0]).query)['q'][0]
        self.assertEqual(q, 'plum AND online_media_type:Images')
        self.assertNotIn('no', [it['url']])

    def test_env_key(self):
        calls = self.no_keys_route({'https://api.si.edu': SmithsonianTest.ROWS},
                                   'SI_API_KEY')
        sources.smithsonian.search('plum', 10)
        self.assertIn('api_key=test-key', calls[0][0])

    def test_content_fallback_when_resource_url_is_missing(self):
        payload = {'response': {'rows': [{
            'title': 'Fallen Plum',
            'content': {'descriptiveNonRepeating': {
                'record_link': 'https://www.si.edu/object/fallen-plum',
                'online_media': {'media': [{
                    'type': 'Images', 'content': 'si_content',
                    'thumbnail': 'si_thumbnail', 'usage': {'access': 'CC0'},
                    'resources': [{'label': 'Missing URL'}]}]}}}}]}}
        self.no_keys_route({'https://api.si.edu': payload})
        [it] = sources.smithsonian.search('plum', 10)
        self.assertEqual(it['url'], 'si_content')
        self.assertEqual(it['thumb'], 'si_thumbnail')


class UnsplashTest(FixtureTest):
    def test_needs_key(self):
        self.no_keys_route({'https://api.unsplash.com': {'results': []}})
        self.assertEqual(sources.unsplash.search('fog', 10), [])

    def test_items(self):
        calls = self.no_keys_route({'https://api.unsplash.com': {'results': [
            {'description': None, 'alt_description': 'Foggy pines',
             'urls': {'full': 'uf', 'small': 'us', 'raw': 'ur'},
             'links': {'html': 'uh'}, 'width': 2000, 'height': 1500}]}},
            'UNSPLASH_ACCESS_KEY')
        [it] = sources.unsplash.search('foggy pines', 10)
        self.assertEqual(it['url'], 'uf')
        self.assertEqual(it['thumb'], 'us')
        self.assertEqual(it['title'], 'Foggy pines')
        self.assertEqual(it['page'], 'uh')
        self.assertEqual(it['lic'], 'Unsplash License')
        self.assertEqual((it['w'], it['h']), (2000, 1500))
        self.assertEqual(calls[0][1]['Authorization'], 'Client-ID test-key')
        self.assertIn('per_page=10', calls[0][0])


class PexelsTest(FixtureTest):
    def test_items(self):
        calls = self.no_keys_route({'https://api.pexels.com': {'photos': [
            {'alt': 'Red canyon', 'photographer': 'A. Shooter',
             'src': {'original': 'po', 'medium': 'pm'}, 'url': 'pu',
             'width': 4000, 'height': 3000}]}}, 'PEXELS_API_KEY')
        [it] = sources.pexels.search('canyon', 10)
        self.assertEqual(it['url'], 'po')
        self.assertEqual(it['thumb'], 'pm')
        self.assertEqual(it['title'], 'Red canyon — A. Shooter')
        self.assertEqual(it['src'], 'Pexels · A. Shooter')
        self.assertEqual(it['lic'], 'Pexels License')
        self.assertEqual(calls[0][1]['Authorization'], 'test-key')
        self.assertIn('per_page=10', calls[0][0])


class PixabayTest(FixtureTest):
    def test_items(self):
        calls = self.no_keys_route({'https://pixabay.com/api/': {'hits': [
            {'tags': 'flowers, pattern', 'largeImageURL': 'lx', 'webformatURL': 'wf',
             'pageURL': 'pp', 'width': 1920, 'height': 1080}]}}, 'PIXABAY_API_KEY')
        [it] = sources.pixabay.search('pattern', 10)
        self.assertEqual(it['url'], 'lx')
        self.assertEqual(it['thumb'], 'wf')
        self.assertEqual(it['title'], 'flowers, pattern')
        self.assertEqual(it['page'], 'pp')
        self.assertEqual(it['src'], 'Pixabay')
        self.assertIn('image_type=illustration', calls[0][0])
        self.assertIn('key=test-key', calls[0][0])

    def test_vector_mode(self):
        calls = self.no_keys_route({'https://pixabay.com/api/': {'hits': []}},
                                   'PIXABAY_API_KEY')
        sources.pixabay.search_vectors('lace', 10)
        self.assertIn('image_type=vector', calls[0][0])


class VamTest(FixtureTest):
    def test_items(self):
        calls = self.route({'https://api.vam.ac.uk/v2/objects/search?': {'records': [
            {'systemNumber': 'O123', 'titles': [{'title': 'Wildflowers'}],
             'objectURL': 'https://collections.vam.ac.uk/item/O123',
             '_images': {'_iiif_image_base_url': 'https://framemark.vam.ac.uk/iiif/O123/',
                         '_primary_thumbnail': 'https://framemark.vam.ac.uk/thumb/O123.jpg'}}]}})
        [it] = sources.vam.search('wildflowers', 10)
        self.assertEqual(it['title'], 'Wildflowers')
        self.assertEqual(it['url'], 'https://framemark.vam.ac.uk/iiif/O123/full/1600,/0/default.jpg')
        self.assertEqual(it['thumb'], 'https://framemark.vam.ac.uk/thumb/O123.jpg')
        self.assertEqual(it['page'], 'https://collections.vam.ac.uk/item/O123')
        self.assertEqual(it['src'], 'V&A')
        self.assertIn('images_exist=1', calls[0][0])
        self.assertIn('page_size=10', calls[0][0])


class RijksTest(FixtureTest):
    def test_title_search_resolves_current_linked_art_image(self):
        calls = self.route({
            'https://data.rijksmuseum.nl/search/collection?title=tulip&imageAvailable=true': {
                'orderedItems': [{'id': 'https://id.rijksmuseum.nl/200100988'}]},
            'https://data.rijksmuseum.nl/search/collection?creator=tulip&imageAvailable=true': {
                'orderedItems': []},
            'https://data.rijksmuseum.nl/200100988?_profile=la-framed': {
                'id': 'https://id.rijksmuseum.nl/200100988',
                'identified_by': [{'type': 'Name', 'content': 'Tulips in a vase'}],
                'shows': [{'id': 'https://id.rijksmuseum.nl/202100988',
                           'type': 'VisualItem'}]},
            'https://data.rijksmuseum.nl/202100988?_profile=la-framed': {
                'digitally_shown_by': [{'id': 'https://id.rijksmuseum.nl/502100988',
                                        'type': 'DigitalObject'}]},
            'https://data.rijksmuseum.nl/502100988?_profile=la-framed': {
                'access_point': [{'id': 'https://iiif.micr.io/PJEZO/full/max/0/default.jpg'}]}})
        out = sources.rijks.search('tulip', 10)
        self.assertEqual(len(out), 1)
        it = out[0]
        self.assertEqual(it['title'], 'Tulips in a vase')
        self.assertEqual(it['url'], 'https://iiif.micr.io/PJEZO/full/max/0/default.jpg')
        self.assertEqual(it['thumb'], it['url'])
        self.assertEqual(it['page'], 'https://id.rijksmuseum.nl/200100988')
        self.assertEqual(it['src'], 'Rijksmuseum')
        qs = [urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
              for url, _ in calls if '/search/collection?' in url]
        self.assertEqual(qs[0]['title'], ['tulip'])
        self.assertEqual(qs[1]['creator'], ['tulip'])
        self.assertTrue(all(q['imageAvailable'] == ['true'] for q in qs))

    def test_creator_search_finds_rembrandt(self):
        calls = self.route({
            'https://data.rijksmuseum.nl/search/collection?title=Rembrandt&imageAvailable=true': {
                'orderedItems': []},
            'https://data.rijksmuseum.nl/search/collection?creator=Rembrandt&imageAvailable=true': {
                'orderedItems': [{'id': 'https://id.rijksmuseum.nl/200200988'}]},
            'https://data.rijksmuseum.nl/200200988?_profile=la-framed': {
                'identified_by': [{'type': 'Name', 'content': 'Portrait by Rembrandt'}],
                'shows': [{'id': 'https://id.rijksmuseum.nl/202200988'}]},
            'https://data.rijksmuseum.nl/202200988?_profile=la-framed': {
                'digitally_shown_by': [{'id': 'https://id.rijksmuseum.nl/502200988'}]},
            'https://data.rijksmuseum.nl/502200988?_profile=la-framed': {
                'access_point': [{'id': 'https://iiif.micr.io/ABC/full/max/0/default.jpg'}]}})
        out = sources.rijks.search('Rembrandt', 10)
        self.assertEqual(len(out), 1)
        it = out[0]
        self.assertEqual(it['title'], 'Portrait by Rembrandt')
        self.assertEqual(it['url'], 'https://iiif.micr.io/ABC/full/max/0/default.jpg')
        q = [urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
             for url, _ in calls if '/search/collection?' in url]
        self.assertEqual(q[1]['creator'], ['Rembrandt'])


class ArchiveTest(FixtureTest):
    def test_items(self):
        calls = self.route({
            'https://archive.org/advancedsearch.php?': {'response': {'docs': [
                {'identifier': 'flower-print', 'title': 'Flower print'}]}},
            'https://archive.org/metadata/flower-print': {
                'metadata': {'licenseurl': 'https://creativecommons.org/publicdomain/mark/1.0/'},
                'files': [{'name': '__ia_thumb.jpg', 'format': 'JPEG Thumb', 'size': '1000'},
                          {'name': 'small.jpg', 'format': 'JPEG', 'size': '5000'},
                          {'name': 'large.jpg', 'format': 'JPEG', 'size': '50000'},
                          {'name': 'text.pdf', 'format': 'Text PDF', 'size': '200000'}]}})
        [it] = sources.archive.search('flower print', 10)
        self.assertEqual(it['title'], 'Flower print')
        self.assertEqual(it['url'], 'https://archive.org/download/flower-print/large.jpg')
        self.assertEqual(it['thumb'], 'https://archive.org/download/flower-print/__ia_thumb.jpg')
        self.assertEqual(it['page'], 'https://archive.org/details/flower-print')
        self.assertEqual(it['lic'], 'https://creativecommons.org/publicdomain/mark/1.0/')
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(calls[0][0]).query)
        self.assertIn('mediatype:image', q['q'][0])
        self.assertEqual(q['fl[]'], ['identifier', 'title'])


class EuropeanaTest(FixtureTest):
    def test_items(self):
        calls = self.no_keys_route({'https://api.europeana.eu/record/v2/search.json?': {
            'items': [{'title': ['Dutch tulips'],
                       'edmIsShownBy': ['https://media.europeana.eu/full.jpg'],
                       'edmPreview': ['https://media.europeana.eu/thumb.jpg'],
                       'edmIsShownAt': ['https://provider.eu/item/1'],
                       'rights': ['http://creativecommons.org/publicdomain/mark/1.0/']}]}},
            'EUROPEANA_API_KEY')
        [it] = sources.europeana.search('tulips', 10)
        self.assertEqual(it['url'], 'https://media.europeana.eu/full.jpg')
        self.assertEqual(it['thumb'], 'https://media.europeana.eu/thumb.jpg')
        self.assertEqual(it['page'], 'https://provider.eu/item/1')
        self.assertEqual(it['lic'], 'http://creativecommons.org/publicdomain/mark/1.0/')
        self.assertEqual(calls[0][1]['X-Api-Key'], 'test-key')
        self.assertIn('media=true', calls[0][0])


class HarvardTest(FixtureTest):
    def test_items(self):
        calls = self.no_keys_route({'https://api.harvardartmuseums.org/object?': {
            'records': [{'objectid': 42, 'title': 'Study of a bird',
                         'baseimageurl': 'https://nrs.harvard.edu/urn-3:HUAM:42',
                         'primaryimageurl': 'https://nrs.harvard.edu/urn-3:HUAM:42/full/400,/0/default.jpg',
                         'url': 'https://harvardartmuseums.org/collections/object/42',
                         'copyright': 'Public domain'}]}}, 'HARVARD_API_KEY')
        [it] = sources.harvard.search('bird', 10)
        self.assertEqual(it['url'], 'https://nrs.harvard.edu/urn-3:HUAM:42/full/full/0/default.jpg')
        self.assertEqual(it['thumb'], 'https://nrs.harvard.edu/urn-3:HUAM:42/full/400,/0/default.jpg')
        self.assertEqual(it['page'], 'https://harvardartmuseums.org/collections/object/42')
        self.assertEqual(it['lic'], 'Public domain')
        self.assertEqual(calls[0][1], None)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(calls[0][0]).query)
        self.assertEqual(q['apikey'], ['test-key'])
        self.assertEqual(q['q'], ['bird'])


class FlickrTest(FixtureTest):
    def test_items(self):
        calls = self.no_keys_route({'https://www.flickr.com/services/rest/?': {
            'photos': {'photo': [{'id': '9', 'owner': 'artist', 'ownername': 'Artist',
                                  'title': 'Garden', 'license': '9',
                                  'url_o': 'https://live.staticflickr.com/9_o.jpg',
                                  'url_m': 'https://live.staticflickr.com/9_m.jpg',
                                  'width_o': 2400, 'height_o': 1800,
                                  'url': 'https://flickr.com/photos/artist/9'}]}}},
            'FLICKR_API_KEY')
        [it] = sources.flickr.search('garden', 10)
        self.assertEqual(it['url'], 'https://live.staticflickr.com/9_o.jpg')
        self.assertEqual(it['thumb'], 'https://live.staticflickr.com/9_m.jpg')
        self.assertEqual(it['page'], 'https://flickr.com/photos/artist/9')
        self.assertEqual(it['lic'], 'CC0')
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(calls[0][0]).query)
        self.assertIn('license', q)
        self.assertIn('9', q['license'][0].split(','))


class BraveTest(FixtureTest):
    def test_items(self):
        calls = self.no_keys_route({'https://api.search.brave.com/res/v1/images/search?': {
            'results': [{'title': 'Birch forest', 'url': 'https://site.test/forest',
                         'properties': {'url': 'https://img.test/forest.jpg',
                                        'width': 2000, 'height': 1200},
                         'thumbnail': {'src': 'https://imgs.brave.com/forest.jpg',
                                       'width': 500, 'height': 300}}]}}, 'BRAVE_API_KEY')
        [it] = sources.brave.search('birch forest', 10)
        self.assertEqual(it['url'], 'https://img.test/forest.jpg')
        self.assertEqual(it['thumb'], 'https://imgs.brave.com/forest.jpg')
        self.assertEqual(it['page'], 'https://site.test/forest')
        self.assertEqual(it['src'], 'Brave Search')
        self.assertEqual(calls[0][1]['X-Subscription-Token'], 'test-key')
        self.assertIn('count=10', calls[0][0])


class ErrorTest(FixtureTest):
    def test_rest_sources_return_empty_on_error(self):
        bad = RuntimeError('offline')
        for name in REST_SOURCES:
            src = sources.REGISTRY[name]
            if src.needs_key:
                self.no_keys(src.needs_key)
            self.route({'': bad})
            self.assertEqual(src.search('q', 5), [], name)

    def test_wrapped_sources_return_empty_on_error(self):
        with mock.patch.object(hunt, 'openverse', side_effect=RuntimeError('off')):
            self.assertEqual(sources.openverse.search('q', 5), [])
        with mock.patch.object(hunt, 'commons_search', side_effect=RuntimeError('off')):
            self.assertEqual(sources.commons.search('q', 5), [])
            self.assertEqual(sources.commons.search_bitmap('q', 5), [])
        with mock.patch.object(hunt, 'commons_categories', side_effect=RuntimeError('off')):
            self.assertEqual(sources.commons.search_categories('q', 5), [])
        with mock.patch.object(hunt, 'iconify_search', side_effect=RuntimeError('off')):
            self.assertEqual(sources.iconify.search('q', 5), [])

    def test_item_shape(self):
        for name in REST_SOURCES:
            src = sources.REGISTRY[name]
            if src.needs_key:
                self.no_keys(src.needs_key)
            self.route({'': {'data': [], 'results': [], 'collection': {'items': []},
                             'response': {'rows': []}, 'photos': [], 'hits': []}})
            for it in src.search('q', 5):
                self.assertEqual(sorted(it), ['h', 'kind', 'lic', 'page', 'src', 'tag',
                                              'thumb', 'title', 'url', 'w'], name)


class WrappedTest(unittest.TestCase):
    ITEMS = [{'title': 'a', 'url': 'u1', 'thumb': None, 'page': 'p', 'src': 's',
              'lic': 'l', 'w': 0, 'h': 0, 'tag': 't', 'kind': 'image'}] * 4

    def test_openverse(self):
        item = dict(self.ITEMS[0])

        def fake(q, tag, svg=False):
            item['tag'] = tag
            return [item] * 4

        with mock.patch.object(hunt, 'openverse', side_effect=fake) as m:
            out = sources.openverse.search('q', 2)
        m.assert_called_with('q', 'q', svg=False)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]['tag'], 'q')
        with mock.patch.object(hunt, 'openverse', return_value=self.ITEMS) as m:
            sources.openverse.search_svg('q', 2)
        m.assert_called_with('q', 'q', svg=True)

    def test_commons(self):
        with mock.patch.object(hunt, 'commons_search', return_value=self.ITEMS) as m:
            out = sources.commons.search('q', 3)
        m.assert_called_with('q', 'q', bitmap=False)
        self.assertEqual(len(out), 3)
        with mock.patch.object(hunt, 'commons_search', return_value=self.ITEMS) as m:
            sources.commons.search_bitmap('q', 3)
        m.assert_called_with('q', 'q', bitmap=True)
        with mock.patch.object(hunt, 'commons_categories', return_value=self.ITEMS) as m:
            out = sources.commons.search_categories('Rookeries', 3)
        m.assert_called_with(['Rookeries'], 'Rookeries')
        self.assertEqual(len(out), 3)

    def test_iconify(self):
        with mock.patch.object(hunt, 'iconify_search', return_value=self.ITEMS) as m:
            out = sources.iconify.search('skull', 2)
        m.assert_called_with('skull', 'skull', limit=2)
        self.assertEqual(len(out), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
