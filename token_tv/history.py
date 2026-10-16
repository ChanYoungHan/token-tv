"""Persistent, credential-free weekly quota observations and daily increments."""
import hashlib
import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


KST = timezone(timedelta(hours=9))
MAX_DAYS = 3660


def day_at(value):
    return datetime.fromtimestamp(value, KST).date().isoformat()


def number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


class UsageHistory:
    def __init__(self, path, accounts, poll_seconds=300):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.gap_seconds = max(900, poll_seconds * 3)
        self.accounts = {}
        for account in accounts:
            identity = '\0'.join((account['provider'], account['key'], account.get('email', '').strip().casefold()))
            self.accounts[account['key']] = {
                'id': hashlib.sha256(identity.encode()).hexdigest(),
                'key': account['key'], 'alias': account['alias'], 'provider': account['provider']}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        os.chmod(self.path, 0o600)
        with self.lock, self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise ValueError('Unsupported history database version')
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS observations (
                    account_id TEXT NOT NULL, observed_at REAL NOT NULL,
                    day TEXT NOT NULL, used_percent REAL NOT NULL, resets_at REAL,
                    high_water REAL NOT NULL, delta_pp REAL, partial INTEGER NOT NULL,
                    reset INTEGER NOT NULL,
                    PRIMARY KEY (account_id, observed_at)
                );
                CREATE TABLE IF NOT EXISTS daily_usage (
                    account_id TEXT NOT NULL, day TEXT NOT NULL,
                    observations INTEGER NOT NULL, intervals INTEGER NOT NULL,
                    used_pp REAL NOT NULL, weekly_percent REAL NOT NULL,
                    first_at REAL NOT NULL, last_at REAL NOT NULL,
                    partial INTEGER NOT NULL, resets INTEGER NOT NULL,
                    PRIMARY KEY (account_id, day)
                );
                PRAGMA user_version=1;
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, snapshot):
        """Only new, verified successful readings can advance the ledger."""
        now = time.time()
        with self.lock, self.connection() as db:
            for key, row in snapshot.get('accounts', {}).items():
                account = self.accounts.get(key)
                observed = row.get('fetched_at')
                if (not account or row.get('status') != 'ok' or not row.get('identity_verified')
                        or not number(observed) or observed <= 0 or observed > now + 60
                        or now - observed > self.gap_seconds):
                    continue
                weekly = next((w for w in row.get('windows', []) if w.get('label') == 'WEEK'), None)
                if not weekly or not number(weekly.get('used_percent')) or not 0 <= weekly['used_percent'] <= 100:
                    continue
                value = float(weekly['used_percent'])
                reset = weekly.get('resets_at')
                reset = float(reset) if number(reset) and reset > 0 else None
                previous = db.execute('SELECT * FROM observations WHERE account_id=? ORDER BY observed_at DESC LIMIT 1',
                                      (account['id'],)).fetchone()
                if previous and observed <= previous['observed_at']:
                    continue
                delta, high, partial, restarted = None, value, 1, 0
                if previous:
                    elapsed = observed - previous['observed_at']
                    old_reset = previous['resets_at']
                    same_cycle = (reset is None and old_reset is None) or (
                        reset is not None and old_reset is not None and abs(reset - old_reset) <= 60)
                    crossed_reset = (old_reset is not None and reset is not None
                                     and reset > old_reset + 60 and observed >= old_reset)
                    partial = int(elapsed > self.gap_seconds)
                    if crossed_reset:
                        # The old cycle's unseen tail cannot be reconstructed.
                        delta, restarted, partial = value, 1, 1
                    elif same_cycle:
                        delta = max(0.0, value - previous['high_water'])
                        high = max(value, previous['high_water'])
                        if value < previous['used_percent']:
                            partial = 1  # correction/unknown reset; never count a rebound twice
                    else:
                        partial = 1  # quota window changed: establish a new baseline
                    # A long gap spanning multiple dates cannot be assigned to one day.
                    if elapsed > self.gap_seconds and day_at(observed) != previous['day']:
                        delta, partial = None, 1
                day = day_at(observed)
                db.execute('INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)',
                           (account['id'], observed, day, value, reset, high, delta, partial, restarted))
                db.execute('''INSERT INTO daily_usage VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(account_id,day) DO UPDATE SET
                        observations=observations+1, intervals=intervals+excluded.intervals,
                        used_pp=used_pp+excluded.used_pp, weekly_percent=excluded.weekly_percent,
                        last_at=excluded.last_at, partial=MAX(partial,excluded.partial),
                        resets=resets+excluded.resets''',
                           (account['id'], day, 1, int(delta is not None), delta or 0, value,
                            observed, observed, partial, restarted))

    def query(self, key=None, start=None, end=None):
        today = datetime.now(KST).date()
        end_day = date.fromisoformat(end) if end else today
        start_day = date.fromisoformat(start) if start else end_day - timedelta(days=13)
        span = (end_day - start_day).days + 1
        if not 1 <= span <= MAX_DAYS or end_day > today:
            raise ValueError('Choose 1 to 3660 days ending no later than today')
        if key is not None and key not in self.accounts:
            raise KeyError(key)
        with self.lock, self.connection() as db:
            available = []
            for account in self.accounts.values():
                bounds = db.execute('SELECT MIN(first_at),MAX(last_at) FROM daily_usage WHERE account_id=?',
                                    (account['id'],)).fetchone()
                available.append({k: account[k] for k in ('key', 'alias', 'provider')} |
                                 {'first_observed_at': bounds[0], 'last_observed_at': bounds[1]})
            selected = self.accounts.get(key) if key else next((self.accounts[a['key']] for a in available if a['first_observed_at']),
                                                             next(iter(self.accounts.values()), None))
            points, before, total, first, last = [], 0, None, None, None
            if selected:
                account_id = selected['id']
                before = db.execute('SELECT COALESCE(SUM(used_pp),0) FROM daily_usage WHERE account_id=? AND day<?',
                                    (account_id, start_day.isoformat())).fetchone()[0]
                totals = db.execute('SELECT SUM(used_pp),SUM(intervals) FROM daily_usage WHERE account_id=?',
                                    (account_id,)).fetchone()
                total = totals[0] if totals[1] else None
                bounds = next(a for a in available if a['key'] == selected['key'])
                first, last = bounds['first_observed_at'], bounds['last_observed_at']
                rows = {row['day']: row for row in db.execute(
                    'SELECT * FROM daily_usage WHERE account_id=? AND day BETWEEN ? AND ? ORDER BY day',
                    (account_id, start_day.isoformat(), end_day.isoformat()))}
                cumulative = before
                for offset in range(span):
                    day = (start_day + timedelta(days=offset)).isoformat()
                    row = rows.get(day)
                    if row:
                        cumulative += row['used_pp']
                    points.append({'date': day,
                                   'used_pp': round(row['used_pp'], 6) if row and row['intervals'] else None,
                                   'cumulative_pp': round(cumulative, 6) if row else None,
                                   'weekly_percent': row['weekly_percent'] if row else None,
                                   'observations': row['observations'] if row else 0,
                                   'partial': bool(row['partial']) if row else False,
                                   'resets': row['resets'] if row else 0,
                                   'in_progress': day == today.isoformat()})
        return {'schema': 1, 'timezone': 'Asia/Seoul', 'unit': 'percentage_points',
                'today': today.isoformat(), 'start': start_day.isoformat(), 'end': end_day.isoformat(),
                'accounts': available, 'account': selected['key'] if selected else None,
                'first_observed_at': first, 'last_observed_at': last, 'days': points,
                'range_used_pp': (round(sum(p['used_pp'] or 0 for p in points), 6)
                                  if any(p['used_pp'] is not None for p in points) else None),
                'total_used_pp': round(total, 6) if total is not None else None,
                'before_range_pp': round(before, 6)}
