import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from token_tv.live import handler
from token_tv.sample import snapshot
from token_tv.state import UsageStore
from token_tv.web_auth import LoginManager, login_url


CLAUDE = """
import sys,termios
assert sys.stdin.isatty()
assert not termios.tcgetattr(0)[3] & termios.ECHO
print('private diagnostic access_token=DO-NOT-EXPORT',flush=True)
print('https://evil.example/steal',flush=True)
print('https://claude.com/cai/oauth/authorize?state=sample&code_challenge=sample',flush=True)
print('Paste code here if prompted >',flush=True)
code=sys.stdin.readline().strip()
sys.exit(0 if code=='SAMPLE-CODE#SAMPLE-STATE' else 1)
"""
DEVICE = """
import pathlib,sys,time
print('https://auth.openai.com/codex/device',flush=True)
print('\\x1b[94mABCD-EFGH\\x1b[0m',flush=True)
while not pathlib.Path(sys.argv[1]).exists(): time.sleep(.03)
"""


def wait_for(manager, key, phase, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = manager.snapshot(key)
        if state['phase'] == phase:
            return state
        time.sleep(.02)
    raise AssertionError(f"Expected {phase}; got {manager.snapshot(key)['phase']}")


@unittest.skipUnless(os.name == 'posix', 'PTY login runs on POSIX hosts')
class LoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.done = self.root / 'device-done'
        self.accounts = [{'key': p + '_a', 'provider': p, 'alias': p.upper() + ' A',
                          'email': p + '@example.invalid', 'source_home': str(self.root / p)}
                         for p in ('claude', 'codex')]
        self.refreshed = threading.Event()
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            manager.close()
        self.temp.cleanup()

    def manager(self, **kwargs):
        def command(account):
            return [sys.executable, '-u', '-c', CLAUDE if account['provider'] == 'claude' else DEVICE, str(self.done)]
        defaults = dict(command=command, on_success=self.refreshed.set,
                        verify=lambda a: {'status': 'ok', 'identity_verified': True})
        defaults.update(kwargs)
        manager = LoginManager(self.accounts, **defaults)
        self.managers.append(manager)
        return manager

    def test_claude_tty_code_success_identity_and_no_output_leaks(self):
        verified = []
        manager = self.manager(verify=lambda account: verified.append(account) or {'status': 'ok', 'identity_verified': True})
        started = manager.start('claude_a')
        self.assertEqual(manager.start('claude_a')['id'], started['id'])
        state = wait_for(manager, 'claude_a', 'waiting')
        self.assertTrue(state['auth_url'].startswith('https://claude.com/'))
        self.assertNotIn('DO-NOT-EXPORT', json.dumps(state))
        with self.assertRaises(ValueError):
            manager.submit('claude_a', 'wrong-session', 'SAMPLE-CODE#SAMPLE-STATE')
        with self.assertRaises(ValueError):
            manager.submit('claude_a', state['id'], 'code\n\x03')
        manager.submit('claude_a', state['id'], 'SAMPLE-CODE#SAMPLE-STATE')
        final = wait_for(manager, 'claude_a', 'succeeded')
        self.assertTrue(self.refreshed.wait(1))
        self.assertIsNone(final['auth_url'])
        self.assertNotIn('SAMPLE-CODE', json.dumps(final))
        self.assertEqual(verified[0]['email'], 'claude@example.invalid')
        self.assertNotIn('claude_a', manager.active_keys())

    def test_device_login_and_wrong_identity_never_signal_success(self):
        manager = self.manager(verify=lambda a: {'status': 'identity_mismatch', 'identity_verified': False})
        manager.start('codex_a')
        state = wait_for(manager, 'codex_a', 'waiting')
        self.assertEqual(state['device_code'], 'ABCD-EFGH')
        self.assertEqual(state['auth_url'], 'https://auth.openai.com/codex/device')
        with self.assertRaises(ValueError):
            manager.submit('codex_a', state['id'], 'SAMPLE-CODE#SAMPLE-STATE')
        self.done.touch()
        final = wait_for(manager, 'codex_a', 'failed')
        self.assertFalse(self.refreshed.is_set())
        self.assertIsNone(final['device_code'])

    def test_cancellation_and_expiry_stop_children_and_clear_links(self):
        manager = self.manager(lifetime=.5)
        manager.start('codex_a')
        state = wait_for(manager, 'codex_a', 'waiting')
        manager.cancel('codex_a', state['id'])
        manager.jobs['codex_a'].thread.join(3)
        self.assertEqual(manager.snapshot('codex_a')['phase'], 'cancelled')
        self.assertIsNotNone(manager.jobs['codex_a'].process.poll())
        self.assertIsNone(manager.snapshot('codex_a')['device_code'])
        manager.start('codex_a')
        wait_for(manager, 'codex_a', 'expired')
        manager.jobs['codex_a'].thread.join(3)
        self.assertIsNotNone(manager.jobs['codex_a'].process.poll())
        self.assertFalse(self.refreshed.is_set())

    def test_failure_does_not_export_stderr_or_exception_and_close_stops_jobs(self):
        manager = self.manager(command=lambda a: [sys.executable, '-c', "import sys; print('secret=DO-NOT-EXPORT');sys.exit(1)"])
        manager.start('claude_a')
        final = wait_for(manager, 'claude_a', 'failed')
        self.assertNotIn('DO-NOT-EXPORT', json.dumps(final))
        other = self.manager()
        other.start('codex_a')
        wait_for(other, 'codex_a', 'waiting')
        other.close()
        self.assertEqual(other.snapshot('codex_a')['phase'], 'cancelled')
        self.assertIsNotNone(other.jobs['codex_a'].process.poll())

    def test_linked_accounts_are_not_replaced_and_urls_are_allowlisted(self):
        self.accounts[0]['snapshot_file'] = '/unused/sample.json'
        manager = self.manager()
        self.assertFalse(manager.available('claude_a'))
        with self.assertRaises(ValueError):
            manager.start('claude_a')
        for url in ('https://evil.example/codex/device', 'http://auth.openai.com/codex/device',
                    'https://auth.openai.com.evil.example/codex/device', 'https://x@auth.openai.com/codex/device',
                    'https://auth.openai.com:444/codex/device'):
            self.assertIsNone(login_url('codex', url))

    def test_provider_polling_skips_account_during_auth(self):
        calls = []
        sample = snapshot(time.time())
        store = UsageStore(self.accounts, fetch=lambda a: calls.append(a['key']) or sample['accounts'][a['key']])
        store.refresh(skip_keys={'claude_a'})
        self.assertEqual(calls, ['codex_a'])
        self.assertEqual(store.snapshot()['accounts']['claude_a']['status'], 'loading')

    def test_http_login_flow_origin_checks_validation_and_no_persisted_session_data(self):
        manager = self.manager()
        store = UsageStore(self.accounts)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(store, logins=manager))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'

        def post(action, data, **headers):
            common = {'Content-Type': 'application/json', 'X-TokenTV-Request': '1', 'Origin': base}
            common.update(headers)
            return urlopen(Request(base + '/auth/claude_a/' + action, data=json.dumps(data).encode(), headers=common))
        try:
            with post('start', {}) as response:
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
                self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
            waiting = wait_for(manager, 'claude_a', 'waiting')
            with urlopen(base + '/auth/claude_a') as response:
                self.assertEqual(json.load(response)['id'], waiting['id'])
            with urlopen(base + '/snapshot') as response:
                data = json.load(response)
                self.assertTrue(data['accounts']['claude_a']['login_available'])
                self.assertNotIn('auth_url', json.dumps(data))
                self.assertNotIn('email', json.dumps(data))
            for headers, payload, status in (({'Origin': 'https://evil.example'}, {}, 403),
                                              ({'Host': 'evil.example'}, {}, 403),
                                              ({'Sec-Fetch-Site': 'cross-site'}, {}, 403),
                                              ({'X-TokenTV-Request': ''}, {}, 400),
                                              ({}, {'unexpected': 'x'}, 400)):
                with self.assertRaises(HTTPError) as raised:
                    post('start', payload, **headers)
                self.assertEqual(raised.exception.code, status)
            with post('code', {'id': waiting['id'], 'code': 'SAMPLE-CODE#SAMPLE-STATE'}) as response:
                self.assertEqual(response.status, 200)
            wait_for(manager, 'claude_a', 'succeeded')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
