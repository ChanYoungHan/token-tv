"""Isolated browser fixture. Synthetic accounts and fake CLIs, no real logins."""
import sys
import tempfile
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from token_tv.live import handler
from token_tv.sample import snapshot
from token_tv.state import UsageStore
from token_tv.web_auth import LoginManager


def main():
    with tempfile.TemporaryDirectory() as directory:
        rows = snapshot(time.time())['accounts']
        accounts = []
        for provider in ('claude', 'codex'):
            key = provider + '_a'
            rows[key].update(status='stale', error_code='http_401')
            accounts.append({'key': key, 'provider': provider, 'alias': provider.upper() + ' A',
                             'email': provider + '@example.invalid', 'source_home': str(Path(directory) / provider)})
        store = UsageStore(accounts, fetch=lambda account: dict(rows[account['key']]))
        store.refresh()

        def verify(account):
            rows[account['key']].update(status='ok', fetched_at=time.time())
            rows[account['key']].pop('error_code', None)
            return rows[account['key']]

        def command(account):
            if account['provider'] == 'claude':
                source = "import sys; print('https://claude.com/cai/oauth/authorize?state=sample',flush=True); code=input('Paste code here > '); sys.exit(0 if code=='SAMPLE-CODE#SAMPLE-STATE' else 1)"
            else:
                source = "import time; print('https://auth.openai.com/codex/device\\nABCD-EFGH',flush=True); time.sleep(15)"
            return [sys.executable, '-u', '-c', source]

        manager = LoginManager(accounts, verify=verify, command=command, on_success=store.refresh)
        try:
            with ThreadingHTTPServer(('0.0.0.0', 18788), handler(store, logins=manager)) as server:
                print('Synthetic web auth fixture on :18788', flush=True)
                server.serve_forever()
        finally:
            manager.close()


if __name__ == '__main__':
    main()
