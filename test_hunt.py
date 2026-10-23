import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

os.environ['IMAGE_HUNT_PORT'] = '8677'

import hunt
import server


class ExtractJsonTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(hunt.extract_json('{"a": 1}'), {'a': 1})

    def test_fenced(self):
        t = 'Sure!\n```json\n{"queries": ["a", "b"]}\n```\nHope that helps.'
        self.assertEqual(hunt.extract_json(t), {'queries': ['a', 'b']})

    def test_prose_wrapped(self):
        t = 'Here is my plan: {"icons":["skull","rat"]} — enjoy!'
        self.assertEqual(hunt.extract_json(t), {'icons': ['skull', 'rat']})

    def test_array(self):
        t = 'picks:\n[{"i":0,"why":"nice one"},{"i":2,"why":"good"}]'
        self.assertEqual(hunt.extract_json(t),
                         [{"i": 0, "why": "nice one"}, {"i": 2, "why": "good"}])

    def test_finds_first(self):
        t = 'nope ) garbled {"bad": on no} tail {"ok": true}'
        self.assertEqual(hunt.extract_json(t), {'ok': True})

    def test_braces_inside_strings(self):
        t = '{"why": "says {hi} \\"ok\\" here"}'
        self.assertEqual(hunt.extract_json(t), {'why': 'says {hi} "ok" here'})

    def test_garbage(self):
        self.assertIsNone(hunt.extract_json('no json at all'))
        self.assertIsNone(hunt.extract_json('{"unterminated": '))
        self.assertIsNone(hunt.extract_json(42))
        self.assertIsNone(hunt.extract_json(None))

    def test_hinted_garbage_mistake(self):
        self.assertIsNone(hunt.extract_json('{"bad""x"}'))


class CommonsUrlTest(unittest.TestCase):
    def test_strip_qs(self):
        self.assertEqual(hunt.strip_qs('https://x/1.jpg?a=b&utm_source=c'), 'https://x/1.jpg')
        self.assertEqual(hunt.strip_qs(''), '')
        self.assertEqual(hunt.strip_qs(None), '')

    def test_retile_thumb(self):
        th = ('https://thumb.wikimedia.org/wikipedia/commons/thumb/f/f0/Foo.jpg/'
              '500px-Foo.jpg')
        self.assertEqual(hunt.retile(th, 3000),
                         'https://thumb.wikimedia.org/wikipedia/commons/thumb/f/f0/Foo.jpg/'
                         '3000px-Foo.jpg')

    def test_items_strips_and_retiles_tif(self):
        ii = {'url': 'https://upload.wikimedia.org/wikipedia/commons/a/ab/Foo.tif?qs=1',
              'thumburl': 'https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Foo.tif/'
                          'lossy-page1-800px-Foo.tif.jpg?u=x',
              'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Foo?w',
              'width': 5000, 'height': 4000}
        page = {'title': 'File:Foo.tif', 'imageinfo': [ii]}
        d = {'query': {'pages': {'1': page}}}
        [item] = hunt.commons_items(d, 'Commons', 'q')
        self.assertEqual(item['url'], 'https://upload.wikimedia.org/wikipedia/commons/thumb/'
                                      'a/ab/Foo.tif/lossy-page1-3000px-Foo.tif.jpg')
        self.assertEqual(item['thumb'], 'https://upload.wikimedia.org/wikipedia/commons/'
                                        'thumb/a/ab/Foo.tif/lossy-page1-800px-Foo.tif.jpg')
        self.assertEqual(item['page'], 'https://commons.wikimedia.org/wiki/File:Foo')
        self.assertEqual(item['w'], 5000)
        self.assertEqual(item['h'], 4000)

    def test_items_skips_bad_ext(self):
        page = {'title': 'File:Doc.pdf',
                'imageinfo': [{'url': 'https://x/Doc.pdf', 'thumbnail': '',
                               'descriptionurl': 'https://c', 'width': 3000, 'height': 3000}]}
        self.assertEqual(hunt.commons_items({'query': {'pages': {'1': page}}}, 's', 'q'), [])


class LoadSavedTest(unittest.TestCase):
    def test_new_shape(self):
        d = {'meta': {'slug': 'x', 'title': 'X', 'query': 'x', 'kind': 'images',
                      'model': 'none', 'created': 'now', 'count': 2},
             'items': [{'title': 'a', 'url': 'u1', 'page': 'p1', 'src': 's1'},
                       {'title': 'b', 'url': 'u2', 'page': 'p2', 'src': 's2'}]}
        meta, items = hunt.load_items(d)
        self.assertEqual(meta['slug'], 'x')
        self.assertEqual(len(items), 2)

    def test_legacy_bare_array(self):
        d = [{'title': 'KYM meme', 'url': 'u1', 'page': 'p1', 'src': 'Know Your Meme'}]
        meta, items = hunt.load_items(d)
        self.assertEqual(meta, {})
        self.assertEqual(items[0]['src'], 'Know Your Meme')

    def test_roundtrip_files(self):
        tmp = tempfile.mkdtemp()
        legacy = [{'title': 't', 'url': 'u', 'page': 'p', 'src': 's'}]
        with open(os.path.join(tmp, 'legacy.json'), 'w') as f:
            json.dump(legacy, f)
        new = {'meta': {'slug': 'n', 'count': 1}, 'items': legacy}
        with open(os.path.join(tmp, 'new.json'), 'w') as f:
            json.dump(new, f)
        with open(os.path.join(tmp, 'legacy.json')) as f:
            meta, items = hunt.load_items(json.load(f))
        self.assertEqual(len(items), 1)
        self.assertEqual(meta, {})
        with open(os.path.join(tmp, 'new.json')) as f:
            meta2, items2 = hunt.load_items(json.load(f))
        self.assertEqual(meta2['slug'], 'n')
        self.assertEqual(len(items2), 1)


class SlugifyTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(hunt.slugify('Skull Art!!', set()), 'skull-art')

    def test_uniqueness(self):
        taken = {'skull', 'skull-2'}
        self.assertEqual(hunt.slugify('skull', taken), 'skull-3')

    def test_max_length(self):
        long = 'a' * 80
        self.assertEqual(hunt.slugify(long, set()), 'a' * 50)
        self.assertEqual(hunt.slugify(long, {'a' * 50}), 'a' * 50 + '-2')

    def test_empty(self):
        self.assertEqual(hunt.slugify('!!!', set()), 'search')
        self.assertEqual(hunt.slugify('', {'search'}), 'search-2')


class SafeNameTest(unittest.TestCase):
    def test_separators(self):
        self.assertEqual(hunt.safe_name('a/b\\c.png'), 'a_b_c.png')
        self.assertNotIn('/', hunt.safe_name('../../etc/passwd'))
        self.assertNotIn('..', hunt.safe_name('../..'))

    def test_empty_fallback(self):
        self.assertEqual(hunt.safe_name(''), 'image')
        self.assertEqual(hunt.safe_name('..'), 'image')
        self.assertEqual(hunt.safe_name('  '), 'image')

    def test_truncate(self):
        self.assertLessEqual(len(hunt.safe_name('x' * 500)), 120)


class StaticResolveTest(unittest.TestCase):
    def test_traversal_rejected(self):
        for bad in ('../SPEC.md', 'saved/../../etc/passwd', '/etc/passwd',
                    'saved/%2e%2e/secret', '..', 'app.html/../../../../etc/passwd'):
            self.assertIsNone(server.resolve_static(bad), bad)

    def test_allowed(self):
        app = server.resolve_static('app.html')
        if os.path.isfile(os.path.join(server.ROOT, 'app.html')):
            self.assertTrue(app and app.endswith('app.html'))
        got = server.resolve_static('saved/woodcut-bottles.json')
        self.assertTrue(got and got.endswith('saved/woodcut-bottles.json'))
        self.assertIsNone(server.resolve_static('saved/nonexistent.json'))
        self.assertIsNone(server.resolve_static('server.py'))
        self.assertIsNone(server.resolve_static('reference/index.html'))


class ModelsTest(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(hunt.DEFAULT_MODEL, 'opencode/glm-5.3-flash-free')
        ids = {m['id'] for m in hunt.MODELS}
        for want in ('none', 'opencode/glm-5.3-flash-free', 'opencode/space-bunny-free',
                     'opencode/step-5-preview-free', 'claude/haiku', 'claude/sonnet',
                     'claude/opus'):
            self.assertIn(want, ids)

    def test_efforts(self):
        by_id = {m['id']: m for m in hunt.MODELS}
        self.assertEqual(by_id['opencode/glm-5.3-flash-free']['efforts'],
                         ['default', 'low', 'high', 'max'])
        self.assertEqual(by_id['claude/haiku']['efforts'],
                         ['default', 'low', 'medium', 'high', 'max'])
        self.assertEqual(by_id['opencode/space-bunny-free']['efforts'], ['default'])

    def test_default_train_hint(self):
        by_id = {m['id']: m for m in hunt.MODELS}
        self.assertEqual(by_id[hunt.DEFAULT_MODEL]['data'], 'train')


class FakeProc:
    def __init__(self, out):
        self.stdout = out


DOCTOR_OK = json.dumps({'ok': True, 'data': {'backends': [
    {'backend': 'claude', 'installed': True, 'auth': 'yes', 'version': '2.1'},
    {'backend': 'codex', 'installed': True, 'auth': 'no', 'version': 'codex-cli'},
    {'backend': 'opencode', 'installed': True, 'auth': 'yes', 'version': '1.18'},
    {'backend': 'gemini', 'installed': True, 'auth': 'unknown', 'version': '0.1'},
    {'backend': 'echo', 'installed': True, 'auth': 'yes', 'version': 'ok'},
    {'backend': 'rtk', 'installed': True, 'version': '/bin/rtk'},
]}})

RANK_OK = json.dumps({'ok': True, 'data': {'models': [
    {'backend': 'opencode', 'id': 'opencode/sonnet-pro', 'variants': [], 'tier': 'big',
     'free': False, 'data': 'zdr', 'score': 70},
    {'backend': 'openrouter', 'id': 'z-ai/glm-5.3', 'variants': [], 'tier': 'big',
     'free': True, 'data': 'zdr', 'score': 66},
    {'backend': 'codex', 'id': 'gpt-6-luna', 'variants': [], 'tier': 'big',
     'free': False, 'data': 'retain', 'score': 60},
    {'backend': 'claude', 'id': 'haiku', 'variants': ['low', 'medium', 'high', 'max'],
     'tier': 'big', 'free': False, 'data': 'retain', 'score': 43.4},
    {'backend': 'claude', 'id': 'sonnet', 'variants': ['low', 'medium', 'high', 'max'],
     'tier': 'big', 'free': False, 'data': 'retain', 'score': 50},
    {'backend': 'opencode', 'id': 'opencode/glm-5.3-flash-free', 'variants': ['low', 'high', 'max'],
     'tier': 'big', 'free': True, 'data': 'train', 'score': 41.8},
    {'backend': 'opencode', 'id': 'opencode/space-bunny-free', 'variants': [], 'tier': 'big',
     'free': True, 'data': 'zdr', 'score': 30},
    {'backend': 'gemini', 'id': 'gemini-2.5-flash', 'variants': [], 'tier': 'big',
     'free': False, 'data': 'retain', 'score': 30},
    7, 'junk', {'backend': 'claude', 'id': 'sonnet', 'variants': [], 'free': False,
                'data': 'retain', 'score': 44},
]}})


class DiscoverModelsTest(unittest.TestCase):
    def setUp(self):
        self._bak = (hunt._ag, hunt.AG_BIN, dict(hunt._MODELS_CACHE))
        hunt._MODELS_CACHE['data'] = None
        hunt._MODELS_CACHE['at'] = 0.0

    def tearDown(self):
        ag, binp, cache = self._bak
        hunt._ag = ag
        hunt.AG_BIN = binp
        hunt._MODELS_CACHE.update(cache)

    def use(self, doctor, rank):
        calls = []

        def fake(args, timeout=30):
            calls.append(list(args))
            if args[0] == 'agents':
                return FakeProc(doctor)
            if args[0] == 'models':
                return FakeProc(rank)
            raise AssertionError(f'unexpected _ag call {args}')

        hunt.AG_BIN = '/fake/ag'
        hunt._ag = fake
        return calls

    def test_discovered(self):
        self.use(DOCTOR_OK, RANK_OK)
        out = hunt.discover_models(refresh=True)
        self.assertNotIn('error', out)
        self.assertEqual(out['ag'], '/fake/ag')
        ids = [m['id'] for m in out['models']]
        self.assertEqual(ids, ['none', 'claude/sonnet', 'claude/haiku',
                               'opencode/glm-5.3-flash-free', 'opencode/space-bunny-free',
                               'gemini/gemini-2.5-flash'])
        self.assertEqual(out['models'][1],
                         {'id': 'claude/sonnet', 'backend': 'claude', 'model': 'sonnet',
                          'label': 'sonnet', 'data': 'retain',
                          'efforts': ['default', 'low', 'medium', 'high', 'max'],
                          'free': False, 'tier': 'big'})
        glm = {m['id']: m for m in out['models']}['opencode/glm-5.3-flash-free']
        self.assertEqual(glm['label'], 'glm-5.3-flash-free (free)')
        self.assertEqual(glm['efforts'], ['default', 'low', 'high', 'max'])
        self.assertEqual(glm['data'], 'train')
        self.assertEqual(glm['tier'], 'big')
        self.assertTrue(glm['free'])
        self.assertEqual(out['default'], hunt.DEFAULT_MODEL)
        back = {b['backend']: b for b in out['backends']}
        self.assertEqual(sorted(back), ['claude', 'codex', 'cursor', 'gemini', 'opencode'])
        self.assertTrue(back['claude']['usable'])
        self.assertEqual(back['claude']['version'], '2.1')
        self.assertFalse(back['codex']['usable'])
        self.assertFalse(back['cursor']['usable'])
        self.assertFalse(back['cursor']['installed'])
        self.assertTrue(back['gemini']['usable'])
        self.assertEqual(back['gemini']['auth'], 'unknown')

    def test_cached(self):
        calls = self.use(DOCTOR_OK, RANK_OK)
        a = hunt.discover_models()
        b = hunt.discover_models()
        self.assertEqual(len(calls), 2)
        self.assertEqual(a['models'], b['models'])
        hunt.discover_models(refresh=True)
        self.assertEqual(len(calls), 4)

    def test_ag_missing(self):
        calls = self.use(DOCTOR_OK, RANK_OK)
        hunt.AG_BIN = None
        out = hunt.discover_models(refresh=True)
        self.assertEqual(out, {'default': 'none', 'models': [hunt.NONE_MODEL],
                               'backends': [], 'ag': None})
        self.assertEqual(calls, [])

    def test_bad_json_falls_back_to_static(self):
        self.use('garbage{', 'garbage{')
        out = hunt.discover_models(refresh=True)
        self.assertIn('error', out)
        self.assertEqual([m['id'] for m in out['models']], [m['id'] for m in hunt.MODELS])
        self.assertEqual(out['default'], hunt.DEFAULT_MODEL)

    def test_usable_only_fallback(self):
        doctor = json.dumps({'ok': True, 'data': {'backends': [
            {'backend': 'claude', 'installed': True, 'auth': 'yes', 'version': '2'},
            {'backend': 'opencode', 'installed': True, 'auth': 'no', 'version': '1'}]}})
        self.use(doctor, 'not json')
        out = hunt.discover_models(refresh=True)
        self.assertIn('error', out)
        ids = [m['id'] for m in out['models']]
        self.assertEqual(ids, ['none', 'claude/haiku', 'claude/sonnet', 'claude/opus'])

    def test_no_usable_backends_gives_none_only(self):
        doctor = json.dumps({'ok': True, 'data': {'backends': [
            {'backend': 'claude', 'installed': True, 'auth': 'no', 'version': '2'}]}})
        self.use(doctor, RANK_OK)
        out = hunt.discover_models(refresh=True)
        self.assertEqual([m['id'] for m in out['models']], ['none'])
        self.assertNotIn('error', out)
        self.assertEqual(out['default'], 'none')

    def test_default_zdr_preference(self):
        rank = json.dumps({'ok': True, 'data': {'models': [
            {'backend': 'claude', 'id': 'opus', 'variants': [], 'free': False,
             'data': 'retain', 'score': 70},
            {'backend': 'opencode', 'id': 'opencode/space-bunny-free', 'variants': [],
             'free': True, 'data': 'zdr', 'score': 30}]}})
        self.use(DOCTOR_OK, rank)
        out = hunt.discover_models(refresh=True)
        self.assertEqual(out['default'], 'opencode/space-bunny-free')

    def test_default_first_real(self):
        doctor = json.dumps({'ok': True, 'data': {'backends': [
            {'backend': 'claude', 'installed': True, 'auth': 'yes', 'version': '2'}]}})
        rank = json.dumps({'ok': True, 'data': {'models': [
            {'backend': 'claude', 'id': 'sonnet', 'variants': [], 'free': False,
             'data': 'retain', 'score': 40}]}})
        self.use(doctor, rank)
        out = hunt.discover_models(refresh=True)
        self.assertEqual(out['default'], 'claude/sonnet')

    def test_model_backend(self):
        self.use(DOCTOR_OK, RANK_OK)
        hunt.discover_models(refresh=True)
        self.assertEqual(hunt.model_backend('opencode/glm-5.3-flash-free'),
                         ('opencode', 'opencode/glm-5.3-flash-free'))
        self.assertEqual(hunt.model_backend('claude/haiku'), ('claude', 'haiku'))
        self.assertEqual(hunt.model_backend('codex/gpt-6-luna'), ('codex', 'gpt-6-luna'))
        self.assertEqual(hunt.model_backend('unknown/x'), ('unknown', 'x'))
        self.assertEqual(hunt.model_backend('none'), (None, 'none'))

    def test_model_label(self):
        self.use(DOCTOR_OK, RANK_OK)
        hunt.discover_models(refresh=True)
        self.assertEqual(hunt.model_label('opencode/glm-5.3-flash-free'),
                         'glm-5.3-flash-free (free)')
        self.assertEqual(hunt.model_label('none'), 'No AI — scripts only')
        self.assertEqual(hunt.model_label('zzz'), 'AI')


class ServerSmokeTest(unittest.TestCase):
    PORT = 8677
    HTTP = f'http://127.0.0.1:{PORT}'

    @classmethod
    def setUpClass(cls):
        if not any('ServerSmokeTest' in a for a in sys.argv):
            raise unittest.SkipTest('smoke runs only when explicitly selected')
        import server as srv
        cls.srv = srv
        cls.srv = srv
        os.makedirs(hunt.SAVED, exist_ok=True)
        cls.httpd = srv.ThreadingHTTPServer(('127.0.0.1', cls.PORT), srv.Handler)
        srv.ensure_worker()
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _get(self, path):
        with urllib.request.urlopen(self.HTTP + path, timeout=30) as r:
            return r.status, r.read().decode('utf-8', 'replace')

    def test_00_static_and_apis(self):
        st, body = self._get('/')
        self.assertEqual(st, 200)
        self.assertIn('<html', body.lower())
        self.assertEqual(self.srv.PORT, self.PORT)
        st, body = self._get('/api/saved')
        self.assertEqual(st, 200)
        entries = json.loads(body)
        slugs = {e['slug'] for e in entries}
        for want in ('halloween-memes', 'spooky-line-art', 'woodcut-bottles'):
            self.assertIn(want, slugs, entries)
        drawn = {e['slug']: e for e in entries}
        with open(os.path.join(hunt.SAVED, 'woodcut-bottles.json')) as f:
            self.assertEqual(drawn['woodcut-bottles']['count'], len(json.load(f)))
        st, body = self._get('/api/models')
        self.assertEqual(st, 200)
        models = json.loads(body)
        ids = {m['id'] for m in models['models']}
        self.assertIn(models['default'], ids | {'none'})
        self.assertIn('none', ids)
        st, body = self._get('/api/jobs')
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body), [])
        req = urllib.request.Request(
            self.HTTP + '/api/chat',
            data=json.dumps({'model': 'none', 'message': 'hi'}).encode(),
            headers={'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as ar:
            urllib.request.urlopen(req, timeout=30)
        with ar.exception:
            self.assertEqual(ar.exception.code, 400)

    def test_10_search_lineart_skull(self):
        req = urllib.request.Request(
            self.HTTP + '/api/search',
            data=json.dumps({'query': 'skull', 'kind': 'lineart', 'model': 'none',
                             'effort': 'default', 'linework_only': False,
                             'limit': 200}).encode(),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=30) as r:
            self.assertEqual(r.status, 200)
            jid = json.loads(r.read().decode())['job']
        self.assertTrue(jid)
        status = None
        for _ in range(60):
            st, body = self._get(f'/api/jobs/{jid}')
            job = json.loads(body)
            status = job['status']
            if status in ('done', 'error'):
                break
            time.sleep(3)
        self.assertTrue(isinstance(status, str))
        self.assertEqual(status, 'done', job.get('log'))
        self.assertTrue(job.get('slug'))
        self.assertIn(job.get('stage'), ('done',), job.get('stage'))
        self.assertTrue(isinstance(job.get('stage_text'), str) and job['stage_text'])
        self.assertIsNone(job.get('progress'))
        self.assertGreater(len(job['items']), 0)
        self.assertLessEqual(len(job['items']), 200)
        kinds = {i.get('kind') for i in job['items']}
        self.assertTrue(any(k in ('icon', 'art') for k in kinds), kinds)
        st, body = self._get('/saved/' + job['slug'] + '.json')
        self.assertEqual(st, 200)
        saved = json.loads(body)
        self.assertEqual(saved['meta']['query'], 'skull')
        self.assertEqual(len(saved['items']), len(job['items']))

    def test_20_download_validation(self):
        req = urllib.request.Request(
            self.HTTP + '/api/download',
            data=json.dumps({'url': 'http://x/y.png', 'slug': 'q'}).encode(),
            headers={'Content-Type': 'application/json'})
        try:
            urllib.request.urlopen(req, timeout=30)
            self.fail('http url must be rejected')
        except urllib.error.HTTPError as e:
            with e:
                self.assertEqual(e.code, 400)
                self.assertIn('https', e.read().decode())


class ChatReplyTest(unittest.TestCase):
    def test_fenced_block_parsed(self):
        reply = ('Try haunted chapels — ossuary imagery reads great in ink.\n'
                 '```searches\n'
                 '[{"query":"sedlec ossuary","kind":"images","linework_only":false,"why":"real bone chapel"},\n'
                 ' {"query":"danse macabre woodcut","kind":"match","linework_only":true,"why":"vintage skeletons"}]\n'
                 '```\nHope that helps!')
        text, actions = hunt.parse_chat_reply(reply)
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]['query'], 'sedlec ossuary')
        self.assertEqual(actions[0]['kind'], 'images')
        self.assertFalse(actions[0]['linework_only'])
        self.assertEqual(actions[0]['why'], 'real bone chapel')
        self.assertEqual(actions[1]['kind'], 'match')
        self.assertTrue(actions[1]['linework_only'])
        self.assertIn('ossuary imagery reads great', text)
        self.assertIn('Hope that helps!', text)
        self.assertNotIn('```searches', text)
        self.assertNotIn('danse', text)

    def test_invalid_block_gives_empty(self):
        for body in ('not json {at][ all', '{"query":"object, not array"}',
                     '["bare","strings"]', '', '   '):
            with self.subTest(body=body):
                reply = f'Sure:\n```searches\n{body}\n```\n'
                text, actions = hunt.parse_chat_reply(reply)
                self.assertEqual(actions, [], body)
                self.assertNotIn('searches', text)
                self.assertTrue(text.startswith('Sure:'))

    def test_no_block(self):
        text, actions = hunt.parse_chat_reply('plain prose reply, no fences')
        self.assertEqual(actions, [])
        self.assertEqual(text, 'plain prose reply, no fences')

    def test_normalised_fields(self):
        reply = ('```searches\n'
                 '[{"query":"  Cats & Dogs\\t ","kind":"weird","linework_only":1,"why":"  many   words  here  "},'
                 '{"kind":"images"},'
                 '"junk",'
                 '{"query":"","kind":"images"}]\n```')
        text, actions = hunt.parse_chat_reply(reply)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['query'], 'Cats & Dogs')
        self.assertEqual(actions[0]['kind'], 'images')
        self.assertTrue(actions[0]['linework_only'])
        self.assertEqual(actions[0]['why'], 'many words here')

    def test_cap_five(self):
        items = [{'query': f'q{i}', 'kind': 'images', 'linework_only': False, 'why': 'x'}
                 for i in range(7)]
        text, actions = hunt.parse_chat_reply('```searches\n' + json.dumps(items) + '\n```')
        self.assertEqual(len(actions), 5)
        self.assertEqual(actions[0]['query'], 'q0')
        self.assertEqual(actions[-1]['query'], 'q4')

    def test_chat_prompt_context(self):
        p = hunt.chat_prompt('picks for a poster?', {'query': 'skull', 'kind': 'lineart',
                                                     'titles': ['a at 1', 'b', 5]})
        self.assertIn('picks for a poster?', p.split('\n\n[context]\n')[0])
        self.assertIn("query='skull'", p)
        self.assertIn('kind=lineart', p)
        self.assertIn('- a at 1', p)
        self.assertIn('- b', p)
        self.assertNotIn('- 5', p)
        self.assertEqual(hunt.chat_prompt('just ask', {}), 'just ask')
        self.assertEqual(hunt.chat_prompt('just ask', None), 'just ask')


class ChatAgentNamingTest(unittest.TestCase):
    def test_chat_names(self):
        self.assertEqual(hunt.chat_name('opencode/glm-5.3-flash-free'),
                         'chat-opencode-glm-5-3-flash-free')
        self.assertEqual(hunt.chat_name('claude/haiku'), 'chat-claude-haiku')
        self.assertEqual(hunt.chat_name('NONE'), 'chat-none')

    def test_never_shares_helper_agent(self):
        for mid in ('opencode/glm-5.3-flash-free', 'claude/haiku', 'none'):
            self.assertNotEqual(hunt.chat_name(mid), hunt.helper_name(mid))
            self.assertTrue(hunt.chat_name(mid).startswith('chat-'))
            self.assertFalse(hunt.helper_name(mid).startswith('chat-'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
