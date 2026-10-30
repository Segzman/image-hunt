import json
import os
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock
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

    def test_retile_only_final_segment(self):
        base = 'https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/'
        self.assertEqual(hunt.retile(base + 'Map_1000px-wide.png/120px-Map_1000px-wide.png', 640),
                         base + 'Map_1000px-wide.png/640px-Map_1000px-wide.png')
        self.assertEqual(hunt.retile(base + 'Foo.jpg', 640), base + 'Foo.jpg')

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

    def test_windows_safe(self):
        r = hunt.safe_name('Title: a? "x" <y>|z*\x01.png')
        for c in ':*?"<>|\x01':
            self.assertNotIn(c, r)
        self.assertTrue(r.endswith('.png'))
        for n in ('CON', 'nul', 'com1.png', 'LPT9.jpg'):
            self.assertNotIn(hunt.safe_name(n).split('.')[0].upper(), hunt._WIN_RESERVED)
        self.assertEqual(hunt.safe_name('Café 東京.png'), 'Café 東京.png')


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


class HelperAgentTest(unittest.TestCase):
    def proc(self, stdout='', returncode=0, stderr=''):
        return type('Proc', (), {'stdout': stdout, 'returncode': returncode,
                                 'stderr': stderr})()

    def test_sandbox_is_in_user_cache_outside_repo(self):
        sandbox = os.path.normcase(os.path.abspath(hunt.HELPER_SANDBOX))
        root = os.path.normcase(os.path.abspath(hunt.ROOT))
        self.assertEqual(os.path.basename(sandbox), 'helper-sandbox')
        self.assertNotEqual(os.path.commonpath([root, sandbox]), root)
        self.assertEqual(sandbox, os.path.normcase(os.path.abspath(os.path.expanduser(
            '~/.cache/image-hunt/helper-sandbox'))))

    def test_existing_helper_moves_from_old_sandbox_once(self):
        name = hunt.helper_name(hunt.DEFAULT_MODEL)
        old = os.path.join(hunt.ROOT, '.helper-sandbox')
        with tempfile.TemporaryDirectory() as sandbox:
            listing = {'agents': [{'name': name, 'dir': old}]}
            calls = []

            def fake(args, timeout=30):
                calls.append(list(args))
                if args == ['agents', 'list', '--json']:
                    return self.proc(json.dumps(listing))
                if args[:3] == ['agents', 'set', name]:
                    listing['agents'][0]['dir'] = args[-1]
                    return self.proc()
                raise AssertionError(args)

            with mock.patch.object(hunt, 'HELPER_SANDBOX', sandbox), \
                    mock.patch.object(hunt, '_ag', side_effect=fake):
                self.assertFalse(hunt._ensure_agent(hunt.DEFAULT_MODEL, name))
                self.assertFalse(hunt._ensure_agent(hunt.DEFAULT_MODEL, name))

            self.assertEqual(calls.count(['agents', 'set', name, '--dir', sandbox]), 1)
            self.assertTrue(os.path.isdir(sandbox))

    def test_effort_and_stage_timeouts_are_synced_before_chat(self):
        name = hunt.helper_name(hunt.DEFAULT_MODEL)
        with tempfile.TemporaryDirectory() as sandbox:
            calls = []

            def fake(args, timeout=30):
                calls.append((list(args), timeout))
                if args == ['agents', 'list', '--json']:
                    return self.proc(json.dumps({'agents': [{'name': name, 'dir': sandbox}]}))
                if args[:2] == ['agents', 'set']:
                    return self.proc()
                if args[:2] == ['chat', 'send']:
                    return self.proc('{"ok":true,"data":{"reply":"ok"}}')
                raise AssertionError(args)

            models = {'models': [{'id': hunt.DEFAULT_MODEL, 'backend': 'opencode',
                                  'efforts': ['default', 'low', 'high', 'max']}]}
            with mock.patch.object(hunt, 'HELPER_SANDBOX', sandbox), \
                    mock.patch.object(hunt, '_ag', side_effect=fake), \
                    mock.patch.object(hunt, 'discover_models', return_value=models):
                self.assertEqual(hunt.ask_helper(hunt.DEFAULT_MODEL, 'default', 'plan',
                                                 {'stage': 'planning'}), 'ok')
                self.assertEqual(hunt.ask_helper(hunt.DEFAULT_MODEL, 'max', 'curate',
                                                 {'stage': 'curating'}), 'ok')

            effort_calls = [args for args, _ in calls if args[:2] == ['agents', 'set']]
            self.assertEqual(effort_calls, [
                ['agents', 'set', name, '--effort', 'low'],
                ['agents', 'set', name, '--effort', 'max'],
            ])
            chats = [(args, timeout) for args, timeout in calls if args[:2] == ['chat', 'send']]
            self.assertEqual([args[args.index('--timeout') + 1] for args, _ in chats],
                             ['90', '120'])
            self.assertEqual([timeout for _, timeout in chats], [100, 130])
            for effort_call, chat in zip(effort_calls, chats):
                self.assertLess(calls.index((effort_call, 30)), calls.index(chat))

    def test_default_effort_clears_previous_effort_when_low_unavailable(self):
        name = hunt.helper_name('opencode/space-bunny-free')
        with tempfile.TemporaryDirectory() as sandbox:
            calls = []

            def fake(args, timeout=30):
                calls.append(list(args))
                if args == ['agents', 'list', '--json']:
                    return self.proc(json.dumps({'agents': [{'name': name, 'dir': sandbox}]}))
                if args[:2] == ['agents', 'set']:
                    return self.proc()
                if args[:2] == ['chat', 'send']:
                    return self.proc('{"ok":true,"data":{"reply":"ok"}}')
                raise AssertionError(args)

            with mock.patch.object(hunt, 'HELPER_SANDBOX', sandbox), \
                    mock.patch.object(hunt, '_ag', side_effect=fake), \
                    mock.patch.object(hunt, 'discover_models', return_value={'models': [
                        {'id': 'opencode/space-bunny-free', 'backend': 'opencode',
                         'efforts': ['default']}]}):
                hunt.ask_helper('opencode/space-bunny-free', 'default', 'plan',
                                {'stage': 'planning'})

            self.assertIn(['agents', 'set', name, '--effort', ''], calls)

    def test_timeout_logs_clear_fallback_message(self):
        name = hunt.helper_name(hunt.DEFAULT_MODEL)
        job = {'stage': 'planning', 'log': []}

        def fake(args, timeout=30):
            if args == ['agents', 'list', '--json']:
                return self.proc(json.dumps({'agents': [{'name': name,
                                                         'dir': hunt.HELPER_SANDBOX}]}))
            if args[:2] == ['agents', 'set']:
                return self.proc()
            if args[:2] == ['chat', 'send']:
                raise subprocess.TimeoutExpired(args, timeout)
            if args == ['agents', 'rm', name]:
                return self.proc()
            raise AssertionError(args)

        with tempfile.TemporaryDirectory() as sandbox:
            agent_dir = os.path.join(sandbox, '.agent')
            meta = os.path.join(agent_dir, 'chats', name + '.meta.json')
            os.makedirs(os.path.dirname(meta))
            with open(meta, 'w') as f:
                f.write('{}')
            with mock.patch.object(hunt, 'HELPER_SANDBOX', sandbox), \
                    mock.patch.object(hunt, 'AGENT_DIR', agent_dir), \
                    mock.patch.object(hunt, '_ag', side_effect=fake), \
                    mock.patch.object(hunt, 'discover_models', return_value={'models': [
                        {'id': hunt.DEFAULT_MODEL, 'backend': 'opencode',
                         'efforts': ['default', 'low', 'high', 'max']}]}):
                self.assertEqual(hunt.ask_helper(hunt.DEFAULT_MODEL, 'default', 'plan', job), '')
            self.assertFalse(os.path.exists(meta))

        self.assertTrue(any('AI helper timed out after 90s — continuing without AI' in line
                            for line in job['log']), job['log'])


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


def source_spec(**kw):
    spec = {'name': 'myart', 'label': 'My Art', 'kinds': ['illustration'],
            'about': 'test source',
            'url': 'https://api.example.com/search?q={query}&limit={limit}',
            'items': 'data.items',
            'fields': {'url': 'u', 'thumb': 't', 'title': 'n', 'page': 'p',
                       'lic': 'l', 'w': 'w', 'h': 'h'}}
    spec.update(kw)
    return spec


class FakeSources:
    def __init__(self, registry, listed=None):
        self.REGISTRY = registry
        self._listed = list(registry) if listed is None else listed

    def available(self):
        return list(self._listed)


class ModesSourcesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._bak = (hunt.sources, hunt.CUSTOM_DIR, hunt.KEYS_PATH)
        hunt.CUSTOM_DIR = os.path.join(self.tmp, 'custom')
        hunt.KEYS_PATH = os.path.join(self.tmp, 'keys.json')
        registry = {
            'openverse': {'name': 'openverse', 'label': 'Openverse',
                          'kinds': list(hunt.MODES), 'about': 'photos',
                          'search': lambda q, n: []},
            'aic': {'name': 'aic', 'label': 'Art Institute',
                    'kinds': ['illustration', 'sketch'], 'about': 'art',
                    'search': lambda q, n: []},
            'unsplash': {'name': 'unsplash', 'label': 'Unsplash',
                         'kinds': ['reference', 'texture'], 'needs_key': 'UNSPLASH_TEST_KEY',
                         'about': 'photos', 'search': lambda q, n: []},
            'iconify': {'name': 'iconify', 'label': 'Iconify',
                        'kinds': ['lineart'], 'about': 'icons',
                        'search': lambda q, n: []},
        }
        hunt.sources = FakeSources(registry, ['openverse', 'aic', 'iconify'])

    def tearDown(self):
        hunt.sources, hunt.CUSTOM_DIR, hunt.KEYS_PATH = self._bak

    def test_mode_selection(self):
        ref = hunt.mode_source_names('reference')
        self.assertIn('openverse', ref)
        self.assertNotIn('iconify', ref)
        self.assertNotIn('aic', ref)
        self.assertIn('iconify', hunt.mode_source_names('lineart'))
        self.assertIn('iconify', hunt.mode_source_names('anything'))
        self.assertIn('aic', hunt.mode_source_names('anything'))

    def test_keyed_source_needs_key(self):
        ref = hunt.mode_source_names('reference')
        self.assertNotIn('unsplash', ref)
        hunt.save_keys({'UNSPLASH_TEST_KEY': 'abc'})
        self.assertIn('unsplash', hunt.mode_source_names('reference'))

    def test_custom_source_by_kind_and_enabled(self):
        hunt.save_custom_source(source_spec(name='tex', kinds=['texture']))
        self.assertIn('tex', hunt.mode_source_names('texture'))
        self.assertNotIn('tex', hunt.mode_source_names('reference'))
        self.assertEqual(hunt.mode_source_names('anything', enabled=['openverse']), ['openverse'])

    def test_fetch_items_uses_selected_sources(self):
        calls = []

        def search(q, n):
            calls.append((q, n))
            return [{'title': q, 'url': f'https://img/{len(calls)}.jpg',
                     'thumb': f'https://img/{len(calls)}-t.jpg', 'w': 800, 'h': 600}]

        hunt.sources.REGISTRY['openverse']['search'] = search
        job = {}
        items = hunt.fetch_items('anything', ['fern'], ['leaf'], [], job,
                                 enabled=['openverse'])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['src'], 'Openverse')
        self.assertEqual(items[0]['tag'], 'fern')
        self.assertEqual(calls, [('fern', hunt.SOURCE_LIMIT)])
        self.assertEqual(job['stage'], 'fetching')

    def test_sources_info(self):
        info = hunt.sources_info()
        names = {s['name'] for s in info['sources']}
        self.assertIn('openverse', names)
        self.assertIn('unsplash', names)
        unsplash = {s['name']: s for s in info['sources']}['unsplash']
        self.assertFalse(unsplash['available'])
        self.assertEqual(unsplash['needs_key'], 'UNSPLASH_TEST_KEY')
        self.assertEqual([m['id'] for m in info['modes']], list(hunt.MODES))

    def test_legacy_modes(self):
        self.assertEqual(hunt.canonical_mode('images'), 'anything')
        self.assertEqual(hunt.canonical_mode('lineart'), 'lineart')
        self.assertEqual(hunt.canonical_mode('nonsense'), 'anything')
        for mode in ('anything', 'reference', 'illustration', 'sketch', 'lineart',
                     'texture', 'scrapbook', 'match'):
            self.assertIn(mode, hunt.MODES)


class CustomSourceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._bak = (hunt.CUSTOM_DIR, hunt.KEYS_PATH, hunt._get_json)
        hunt.CUSTOM_DIR = os.path.join(self.tmp, 'custom')
        hunt.KEYS_PATH = os.path.join(self.tmp, 'keys.json')

    def tearDown(self):
        hunt.CUSTOM_DIR, hunt.KEYS_PATH, hunt._get_json = self._bak

    def test_validate_accepts_good_spec(self):
        got = hunt.validate_source_spec(source_spec())
        self.assertIsNotNone(got)
        self.assertEqual(got['name'], 'myart')
        self.assertIsNone(hunt.source_spec_error(got))

    def test_validate_rejects_bad_specs(self):
        bad = [
            source_spec(url='http://api.example.com/?q={query}'),
            source_spec(url='https://api.example.com/?q=no-placeholder'),
            source_spec(fields={'url': 'u'}),
            source_spec(items='data..items'),
            source_spec(fields={'url': 'u', 'thumb': 'bad path'}),
            source_spec(name='../escape'),
            source_spec(kinds=['halloween']),
            source_spec(key_env='lowercase'),
            'not a dict',
        ]
        for spec in bad:
            with self.subTest(spec=spec):
                self.assertIsNone(hunt.validate_source_spec(spec))
                self.assertIsNotNone(hunt.source_spec_error(spec))

    def test_dotted_paths(self):
        data = {'images': [{'url': 'https://x/1.jpg'}, {'url': 'https://x/2.jpg'}],
                'nested': {'deep': [{'v': 3}]}}
        self.assertEqual(hunt.dotted_get(data, 'images.1.url'), 'https://x/2.jpg')
        self.assertEqual(hunt.dotted_get(data, 'nested.deep.0.v'), 3)
        self.assertEqual(hunt.dotted_get(data, '$.nested.deep.0.v'), 3)
        self.assertIsNone(hunt.dotted_get(data, 'images.9.url'))
        self.assertIsNone(hunt.dotted_get(data, 'nope.deep'))
        self.assertEqual(hunt.dotted_get(data, ''), data)

    def test_custom_search(self):
        captured = {}

        def fake_get(url, timeout, max_bytes=None, extra_headers=None):
            captured.update(url=url, timeout=timeout, max_bytes=max_bytes)
            return {'data': {'items': [
                {'u': 'https://img/a.jpg', 't': 'https://img/a-t.jpg', 'n': 'A',
                 'p': 'https://page/a', 'l': 'CC0', 'w': '1200', 'h': '800'},
                {'u': 'http://insecure/b.jpg', 't': 'https://img/b-t.jpg', 'n': 'B'},
                {'u': 'https://img/c.svg', 't': 'https://img/c-t.png', 'n': 'C'},
            ]}}

        hunt._get_json = fake_get
        out = hunt.custom_search(source_spec(), 'red fox', 2)
        self.assertEqual(len(out), 2)
        self.assertIn('q=red+fox', captured['url'])
        self.assertIn('limit=2', captured['url'])
        self.assertEqual(captured['timeout'], 20)
        self.assertEqual(captured['max_bytes'], 5 * 1024 * 1024)
        self.assertEqual(out[0]['src'], 'My Art')
        self.assertEqual(out[0]['url'], 'https://img/a.jpg')
        self.assertEqual(out[0]['thumb'], 'https://img/a-t.jpg')
        self.assertEqual(out[0]['w'], 1200)
        self.assertEqual(out[1]['kind'], 'art')

    def test_custom_search_error_is_empty(self):
        def boom(*a, **k):
            raise OSError('down')

        hunt._get_json = boom
        self.assertEqual(hunt.custom_search(source_spec(), 'x', 5), [])

    def test_save_and_delete_custom_source(self):
        hunt.save_custom_source(source_spec())
        self.assertTrue(os.path.isfile(os.path.join(hunt.CUSTOM_DIR, 'myart.json')))
        self.assertEqual([s['name'] for s in hunt.load_custom_sources()], ['myart'])
        self.assertTrue(hunt.delete_custom_source('myart'))
        self.assertFalse(hunt.delete_custom_source('myart'))


class ChatSourceBlockTest(unittest.TestCase):
    def test_source_block_parsed(self):
        spec = source_spec()
        reply = ('Try this source.\n'
                 '```searches\n[{"query":"fern","kind":"scrapbook","why":"plates"}]\n```\n'
                 '```source\n' + json.dumps(spec) + '\n```\n')
        text, actions = hunt.parse_chat_reply(reply)
        self.assertIn('Try this source.', text)
        self.assertNotIn('```source', text)
        self.assertNotIn('```searches', text)
        searches = [a for a in actions if a.get('type') != 'source']
        sources = [a for a in actions if a.get('type') == 'source']
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0]['kind'], 'scrapbook')
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['spec']['name'], 'myart')
        self.assertIsNone(sources[0]['error'])

    def test_bad_source_block_is_marked(self):
        reply = '```source\n{"name":"../x"}\n```'
        text, actions = hunt.parse_chat_reply(reply)
        self.assertEqual(text, '')
        self.assertEqual(len(actions), 1)
        self.assertTrue(actions[0]['error'])


class V5RankingFilteringTest(unittest.TestCase):
    def test_rank_uses_relevance_resolution_and_mode_bonus(self):
        items = [
            {'title': 'Red fox woodcut', 'src': 'Commons', 'url': 'https://x/fox.jpg',
             'w': 3000, 'h': 3000, 'line': .5},
            {'title': 'Unrelated', 'src': 'Commons', 'url': 'https://x/other.jpg',
             'w': 600, 'h': 600, 'line': 0},
        ]
        out = hunt.rank_items(items, 'red fox', ['fox woodcut'], 'match')
        self.assertEqual(out[0]['title'], 'Red fox woodcut')
        self.assertEqual(out[0]['score'], .9)
        self.assertLess(out[1]['score'], out[0]['score'])

    def test_near_duplicates_keep_best_ranked_item(self):
        items = [
            {'title': 'fox in the woods!', 'src': 'Commons', 'url': 'https://x/two.jpg', 'score': .8},
            {'title': 'Fox — in the woods', 'src': 'Commons', 'url': 'https://x/one.jpg', 'score': .9},
            {'title': 'A different fox', 'src': 'Openverse', 'url': 'https://y/one.png', 'score': .7},
            {'title': 'Another title', 'src': 'Openverse', 'url': 'https://y/three.jpg', 'score': .6},
        ]
        out = hunt.dedupe_items(items)
        self.assertEqual([i['url'] for i in out], ['https://x/one.jpg', 'https://y/three.jpg'])

    def test_filter_items_composes_source_size_orientation_licence_kind_and_sort(self):
        items = [
            {'title': 'landscape', 'src': 'Photos', 'url': 'https://x/a.jpg', 'kind': 'image',
             'w': 2400, 'h': 1200, 'lic': 'CC BY', 'score': .4},
            {'title': 'square', 'src': 'Photos', 'url': 'https://x/b.jpg', 'kind': 'image',
             'w': 1400, 'h': 1400, 'lic': 'Public Domain', 'score': .9},
            {'title': 'svg', 'src': 'Icons', 'url': 'https://x/c.svg', 'kind': 'icon',
             'lic': 'CC0', 'score': .8},
        ]
        out = hunt.filter_items(items, {'source': 'Photos', 'min_size': 1000,
                                        'orientation': 'landscape', 'license': 'cc',
                                        'kind': 'image', 'sort': 'largest'})
        self.assertEqual([i['title'] for i in out], ['landscape'])

    def test_filter_items_public_domain_svg_and_best_sort(self):
        items = [
            {'title': 'small PD', 'url': 'https://x/a.jpg', 'w': 900, 'h': 900,
             'lic': 'Public domain', 'score': .2},
            {'title': 'large CC0', 'url': 'https://x/b.jpg', 'w': 1200, 'h': 1000,
             'lic': 'CC0', 'score': .9},
            {'title': 'SVG CC', 'url': 'https://x/c.svg', 'kind': 'art',
             'lic': 'CC BY 4.0', 'score': .5},
        ]
        pd = hunt.filter_items(items, {'license': 'pd'})
        self.assertEqual([i['title'] for i in pd], ['large CC0', 'small PD'])
        svg = hunt.filter_items(items, {'kind': 'svg', 'sort': 'source'})
        self.assertEqual([i['title'] for i in svg], ['SVG CC'])


class V5FetchTest(unittest.TestCase):
    def setUp(self):
        self._sources = hunt.sources
        self._cache = hunt._SOURCE_CACHE.copy()
        hunt._SOURCE_CACHE.clear()

    def tearDown(self):
        hunt.sources = self._sources
        hunt._SOURCE_CACHE.clear()
        hunt._SOURCE_CACHE.update(self._cache)

    def setup_sources(self, registry):
        hunt.sources = FakeSources(registry)

    def test_source_response_cache_hits(self):
        calls = []

        def search(q, limit):
            calls.append(q)
            return [{'title': q, 'url': 'https://x/fern.jpg', 'w': 1200, 'h': 800}]

        self.setup_sources({'fast': {'name': 'fast', 'label': 'Fast', 'kinds': ['anything'],
                                     'about': '', 'search': search}})
        for _ in range(2):
            hunt.fetch_items('anything', ['fern'], [], [], {}, enabled=['fast'])
        self.assertEqual(calls, ['fern'])

    def test_partial_items_publish_before_slow_source_finishes(self):
        release = threading.Event()
        ready = threading.Event()

        def fast(q, limit):
            return [{'title': 'fast', 'url': 'https://x/fast.jpg', 'w': 1200, 'h': 800}]

        def slow(q, limit):
            ready.set()
            release.wait(2)
            return [{'title': 'slow', 'url': 'https://x/slow.jpg', 'w': 1200, 'h': 800}]

        self.setup_sources({name: {'name': name, 'label': name, 'kinds': ['anything'],
                                   'about': '', 'search': fn}
                            for name, fn in (('fast', fast), ('slow', slow))})
        job = {}
        thread = threading.Thread(target=hunt.fetch_items,
                                  args=('anything', ['fern'], [], [], job),
                                  kwargs={'enabled': ['fast', 'slow']})
        thread.start()
        self.assertTrue(ready.wait(1))
        deadline = time.time() + 1
        while not job.get('items') and time.time() < deadline:
            time.sleep(.005)
        self.assertTrue(job.get('partial'))
        self.assertEqual([i['title'] for i in job['items']], ['fast'])
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(job['partial'])
        self.assertEqual(len(job['items']), 2)

    def test_slow_source_is_capped_by_future_timeout(self):
        def slow(q, limit):
            time.sleep(.08)
            return [{'title': 'late', 'url': 'https://x/late.jpg'}]

        self.setup_sources({'slow': {'name': 'slow', 'label': 'Slow', 'kinds': ['anything'],
                                     'about': '', 'search': slow}})
        job = {}
        start = time.monotonic()
        with mock.patch.object(hunt, 'SOURCE_TIMEOUT', .01):
            out = hunt.fetch_items('anything', ['fern'], [], [], job, enabled=['slow'])
        self.assertLess(time.monotonic() - start, .06)
        self.assertEqual(out, [])
        self.assertFalse(job['partial'])
        self.assertEqual(job['progress'], [1, 1])


class V5CurateTest(unittest.TestCase):
    def test_curate_timeout_keeps_scored_order(self):
        items = [{'title': 'low', 'url': 'https://x/low.jpg', 'score': .2},
                 {'title': 'high', 'url': 'https://x/high.jpg', 'score': .9}]
        with mock.patch.object(hunt, 'ask_helper', return_value=''), \
                mock.patch.object(hunt, 'model_label', return_value='AI'):
            out = hunt.curate('model', 'default', 'fox', items, {})
        self.assertEqual([i['title'] for i in out], ['high', 'low'])

    def test_curate_sends_top_60_and_never_drops_items(self):
        items = [{'title': f'title {i}', 'src': 'A', 'url': f'https://x/{i}.jpg',
                  'w': 1000 + i, 'h': 800, 'line': .25, 'score': i / 100}
                 for i in range(75)]
        job = {}
        prompts = []

        def ask(model, effort, prompt, job):
            prompts.append(prompt)
            return '{"picks":[{"i":0,"why":"top result"}]}'

        with mock.patch.object(hunt, 'ask_helper', side_effect=ask), \
                mock.patch.object(hunt, 'model_label', return_value='AI'):
            out = hunt.curate('model', 'default', 'fox', items, job)
        self.assertEqual(len(out), 75)
        self.assertEqual(out[0]['title'], 'title 74')
        self.assertTrue(out[0]['pick'])
        prompt = prompts[0]
        self.assertIn('title 74', prompt)
        self.assertNotIn('title 14', prompt)
        self.assertIn('1074×800', prompt)
        self.assertIn('line=', prompt)


class V5ServerConfigTest(unittest.TestCase):
    def test_worker_count_default_env_and_invalid_value(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(server.worker_count(), 3)
        with mock.patch.dict(os.environ, {'IMAGE_HUNT_WORKERS': '5'}):
            self.assertEqual(server.worker_count(), 5)
        with mock.patch.dict(os.environ, {'IMAGE_HUNT_WORKERS': 'bad'}):
            self.assertEqual(server.worker_count(), 3)

    def test_ensure_worker_starts_configured_pool(self):
        started = []

        class FakeThread:
            def __init__(self, target, name, daemon):
                self.name = name

            def start(self):
                started.append(self.name)

        with mock.patch.object(server, 'worker_count', return_value=3), \
                mock.patch.object(server.threading, 'enumerate', return_value=[]), \
                mock.patch.object(server.threading, 'Thread', FakeThread):
            server.ensure_worker()
        self.assertEqual(started, ['hunt-worker-1', 'hunt-worker-2', 'hunt-worker-3'])

    def test_job_payload_exposes_only_live_partial_items(self):
        item = {'url': 'https://x/a.jpg'}
        self.assertEqual(server.job_payload({'status': 'running', 'partial': True,
                                             'items': [item]})['items'], [item])
        self.assertEqual(server.job_payload({'status': 'running', 'partial': False,
                                             'items': [item]})['items'], [])
        self.assertEqual(server.job_payload({'status': 'done', 'items': [item]})['items'], [item])


if __name__ == '__main__':
    unittest.main(verbosity=2)


class IIIFDedupe(unittest.TestCase):
    def test_iiif_images_not_collapsed(self):
        mk = lambda i: {'title': f't{i}', 'src': 'AIC', 'url': f'https://www.artic.edu/iiif/2/id-{i}/full/1686,/0/default.jpg'}
        self.assertEqual(len(hunt.dedupe_items([mk(1), mk(2), mk(3)])), 3)
        self.assertEqual(len(hunt.dedupe_items([mk(1), dict(mk(1), title='x')])), 1)
