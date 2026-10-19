import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from token_tv import cli
from token_tv.display import STYLES, overview_rows, render_page
from token_tv.sample import H, snapshot
from token_tv.space import readings, seed_for


SAMPLE = [('claude_a', 'CLAUDE A', 'claude', [('5H', 24, H)]),
          ('codex_a', 'CODEX A', 'codex', [('WEEK', 57, H)]),
          ('codex_b', 'CODEX B', 'codex', [('WEEK', 83, H)])]


class ClockAccountTests(unittest.TestCase):
    def test_two_codex_accounts_get_distinct_stable_rows(self):
        data = snapshot(sample=SAMPLE)
        rows = overview_rows(data)
        self.assertEqual([r['alias'] for r in rows], ['CLAUDE A', 'CODEX A', 'CODEX B'])
        self.assertEqual([r['windows'][0]['used_percent'] for r in rows], [24, 57, 83])
        data['accounts']['codex_a'].update(status='auth_required', windows=[])
        data['accounts']['codex_b']['status'] = 'stale'
        rows = overview_rows(data)
        self.assertEqual([r['key'] for r in rows], ['claude_a', 'codex_a', 'codex_b'])
        self.assertEqual(rows[1]['windows'], [])
        self.assertEqual(rows[2]['status'], 'stale')

    def test_three_accounts_of_one_service_and_overflow(self):
        data = snapshot(sample=[(f'codex_{k}', f'CODEX {k.upper()}', 'codex', [('WEEK', 10+i, H)])
                                for i,k in enumerate('abc')])
        self.assertEqual([r['alias'] for r in overview_rows(data)], ['CODEX A', 'CODEX B', 'CODEX C'])
        data['accounts'].update(snapshot(sample=SAMPLE[:1])['accounts'])
        self.assertEqual(len(overview_rows(data)), 3)
        self.assertEqual(len(data['accounts']), 4)

    def test_all_faces_render_three_accounts_and_unknowns(self):
        data = snapshot(sample=SAMPLE)
        for missing in (False, True):
            if missing:
                data['accounts']['codex_b'].update(status='auth_required', windows=[])
            for style in STYLES:
                with self.subTest(style=style, missing=missing):
                    body = render_page(data, style=style)
                    image = Image.open(io.BytesIO(body))
                    self.assertEqual(image.size, (240, 240))
                    self.assertLess(len(body), 400000 if image.format == 'GIF' else 60000)

    def test_space_labels_distinguish_identical_provider_mascots(self):
        data = snapshot(sample=SAMPLE)
        rows = readings(data)
        self.assertEqual([row[1] for row in rows], ['CLAUDE A', 'CODEX A', 'CODEX B'])
        original = seed_for(rows, now=1800)
        data['accounts']['codex_b']['alias'] = 'CODEX C'
        self.assertNotEqual(seed_for(readings(data), now=1800), original)


class AddAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.json'
        self.existing = {'key': 'codex_a', 'alias': 'CODEX A', 'provider': 'codex',
                         'email': 'a@example.invalid', 'source_home': str(self.root / 'homes/codex_a')}
        self.data = {'accounts': [self.existing], 'poll_seconds': 300, 'display_style': 'retro',
                     'device_url': 'http://192.0.2.7'}
        self.config.write_text(json.dumps(self.data))

    def add(self, email='b@example.invalid'):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main(['add-account', '--config', str(self.config), '--provider', 'codex', '--email', email])

    def test_adds_b_without_opening_or_changing_existing_login_and_db(self):
        home = Path(self.existing['source_home'])
        home.mkdir(parents=True)
        marker = home / 'auth.json'
        marker.write_text('synthetic-login-marker')
        history = self.root / 'history.sqlite3'
        history.write_bytes(b'synthetic-history-marker')
        self.assertEqual(self.add(), 0)
        result = json.loads(self.config.read_text())
        self.assertEqual(result['accounts'][0], self.existing)
        self.assertEqual(result['accounts'][1], {'key': 'codex_b', 'alias': 'CODEX B', 'provider': 'codex',
            'email': 'b@example.invalid', 'source_home': str(self.root / 'homes/codex_b')})
        self.assertEqual({k:v for k,v in result.items() if k!='accounts'}, {k:v for k,v in self.data.items() if k!='accounts'})
        self.assertEqual(marker.read_text(), 'synthetic-login-marker')
        self.assertEqual(history.read_bytes(), b'synthetic-history-marker')
        self.assertEqual(self.config.stat().st_mode & 0o077, 0)

    def test_adds_c_and_refuses_fourth_and_duplicates(self):
        self.assertEqual(self.add(), 0)
        self.assertEqual(self.add('c@example.invalid'), 0)
        before = self.config.read_bytes()
        self.assertEqual(self.add('d@example.invalid'), 1)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.add(' A@EXAMPLE.INVALID '), 1)
        self.assertEqual(self.config.read_bytes(), before)

    def test_keeps_existing_homes_and_missing_or_invalid_configs(self):
        reserved = self.root / 'homes/codex_b'
        reserved.mkdir(parents=True)
        self.assertEqual(self.add(), 0)
        self.assertEqual(json.loads(self.config.read_text())['accounts'][-1]['key'], 'codex_c')
        before = self.config.read_bytes()
        self.assertEqual(self.add('not-an-email'), 1)
        self.assertEqual(self.config.read_bytes(), before)
        self.config.unlink()
        self.assertEqual(self.add(), 1)
        self.assertFalse(self.config.exists())


if __name__ == '__main__':
    unittest.main()
