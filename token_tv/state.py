"""Polling owns provider requests; HTTP readers only see cached state."""
import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from token_tv.sources import fetch_account


class UsageStore:
    def __init__(self, accounts, fetch=fetch_account, auto_refresh=False):
        self.accounts = accounts
        self.fetch = fetch
        self.lock = threading.Lock()
        self.auto_refresh = auto_refresh
        self.account_locks = {a['key']: threading.RLock() for a in accounts}
        self.pending = set()
        self.retries = {}
        self.recovery = {}
        self.updated_at = 0
        self.rows = {a["key"]: {"key": a["key"], "alias": a["alias"], "provider": a["provider"],
                               "status": "loading", "windows": [], "fetched_at": 0,
                               "identity_verified": False, "source": "mini"}
                     for a in accounts}

    def retry_available(self, key):
        return any(a['key'] == key and a['provider'] in ('claude', 'codex') and a.get('source_home')
                   and not any(a.get(k) for k in ('snapshot_file', 'codexbar_command', 'codexbar_url'))
                   for a in self.accounts)

    def request_retry(self, key):
        if not self.retry_available(key):
            raise KeyError(key)
        with self.lock:
            previous = self.retries.get(key, {})
            if previous.get('phase') in ('queued', 'running') or time.monotonic() < previous.get('after', 0):
                return
            self.pending.add(key)
            self.retries[key] = {'phase': 'queued', 'message': '연결 재시도를 기다립니다.',
                                 'after': time.monotonic() + 30}

    def take_retries(self):
        with self.lock:
            keys, self.pending = self.pending, set()
            return keys

    def _allow_rotation(self, key):
        with self.lock:
            previous = self.recovery.get(key, {})
            if (time.monotonic() < previous.get('after', 0)
                    and time.time() < previous.get('next_attempt_at', 0)):
                return False
            attempts = min(previous.get('attempts', 0) + 1, 4)
            delay = min(3600, 300 * 3 ** (attempts - 1))
            self.recovery[key] = {'attempts': attempts, 'after': time.monotonic() + delay,
                                  'last_attempt_at': time.time(), 'next_attempt_at': time.time() + delay}
            return True

    def _fetch_one(self, account):
        key = account['key']
        with self.account_locks[key]:
            with self.lock:
                if self.retries.get(key, {}).get('phase') == 'queued':
                    self.retries[key].update(phase='running', message='사용량과 인증 연결을 확인하고 있습니다.')
            if self.auto_refresh and self.retry_available(key) and account['provider'] == 'claude':
                row = self.fetch(dict(account, refresh_with_cli=True), before_refresh=lambda: self._allow_rotation(key))
            elif self.retry_available(key) and account['provider'] == 'claude':
                row = self.fetch(dict(account, refresh_with_cli=False))
            else:
                row = self.fetch(account)
            with self.lock:
                if row['status'] in ('ok', 'quota_unavailable') and row.get('identity_verified'):
                    self.recovery.pop(key, None)
                job = self.retries.get(key)
                if job and job['phase'] in ('running', 'queued', 'failed'):
                    self.pending.discard(key)
                    good = row['status'] in ('ok', 'quota_unavailable') and row.get('identity_verified')
                    auth = row.get('error_code') in ('http_401', 'http_403', 'auth_required', 'identity_mismatch') or row['status'] in ('auth_required', 'identity_mismatch')
                    waiting = self.recovery.get(key, {}).get('next_attempt_at', 0) > time.time()
                    message = ('연결 확인 완료. 사용량과 시계 화면을 갱신합니다.' if good else
                               '인증 갱신 재시도 대기 중입니다. 기다리거나 다시 로그인해 주세요.' if auth and waiting else
                               '인증을 확인하지 못했습니다. 다시 로그인해 주세요.' if auth else
                               '조회를 완료하지 못했습니다. 네트워크와 계정 상태를 확인해 주세요.')
                    job.update(phase='succeeded' if good else 'failed', message=message)
            return row

    def refresh(self, skip_keys=(), only_keys=None):
        accounts = [a for a in self.accounts if a['key'] not in skip_keys
                    and (only_keys is None or a['key'] in only_keys)]
        with self.lock:
            for key in skip_keys:
                job = self.retries.get(key)
                if job and job['phase'] == 'queued':
                    job.update(phase='failed', message='로그인 진행 중입니다. 로그인 완료 후 다시 시도해 주세요.')
        rows = []
        if accounts:
            with ThreadPoolExecutor(max_workers=min(4, len(accounts))) as executor:
                rows = list(executor.map(self._fetch_one, accounts))
        with self.lock:
            for row in rows:
                previous = self.rows[row["key"]]
                if row["status"] in ("ok", "quota_unavailable"):
                    row["last_success_at"] = row["fetched_at"]
                elif row["status"] != "identity_mismatch" and previous.get("windows") and previous.get("last_success_at"):
                    row["error_code"] = row.get("error_code", row["status"])
                    row["status"] = "stale"
                    row["windows"] = previous["windows"]
                    if 'subscription' in previous:
                        row['subscription'] = previous['subscription']
                    row["last_success_at"] = previous["last_success_at"]
                    row["identity_verified"] = previous.get("identity_verified", False)
                self.rows[row["key"]] = row
            self.updated_at = int(time.time())

    def snapshot(self):
        with self.lock:
            rows = copy.deepcopy(self.rows)
            updated = self.updated_at
            retries, recovery = copy.deepcopy(self.retries), copy.deepcopy(self.recovery)
        now = time.time()
        for row in rows.values():
            key = row['key']
            row['retry_available'] = self.retry_available(key)
            job = retries.get(key, {})
            row['retry'] = {k: job[k] for k in ('phase', 'message') if k in job}
            row['retry']['retry_after_seconds'] = max(0, int(job.get('after', 0) - time.monotonic() + .999))
            state = recovery.get(key, {})
            row['auth_recovery'] = {k: state[k] for k in ('last_attempt_at', 'next_attempt_at') if k in state}
            row['auth_recovery']['enabled'] = self.auto_refresh and self.retry_available(key)
            if row["status"] == "ok" and now - row.get("last_success_at", row["fetched_at"]) > 900:
                row["status"] = "stale"
                row.setdefault("error_code", "snapshot_old")
            by_label = {w["label"]: w for w in row["windows"]}
            for prefix, label in (("session", "5H"), ("weekly", "WEEK")):
                value = by_label.get(label, {})
                row[prefix + "_percent"] = value.get("used_percent")
                reset = value.get("resets_at")
                row[prefix + "_reset_minutes"] = max(0, int((reset - now + 59) // 60)) if reset else None
        return {"schema": 1, "updated_at": updated, "accounts": rows}
