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
MAX_HOURS = 2160


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

    def query(self, key=None, start=None, end=None, granularity='day'):
        if granularity == 'hour':
            return self.query_hour(key, start, end)
        if granularity != 'day':
            raise ValueError('Unknown history granularity')
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
            points, events, before, total, first, last = [], [], 0, None, None, None
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
                                   'weekly_observed_at': row['last_at'] if row else None,
                                   'observations': row['observations'] if row else 0,
                                   'partial': bool(row['partial']) if row else False,
                                   'resets': row['resets'] if row else 0,
                                   'in_progress': day == today.isoformat()})
                events = self.reset_events(db, account_id,
                                           datetime.combine(start_day, datetime.min.time(), KST).timestamp(),
                                           datetime.combine(end_day + timedelta(days=1), datetime.min.time(), KST).timestamp(),
                                           before)
        return {'schema': 1, 'timezone': 'Asia/Seoul', 'unit': 'percentage_points', 'granularity': 'day',
                'weekly_unit': 'percent', 'weekly_aggregation': 'last_successful_observation',
                'today': today.isoformat(), 'start': start_day.isoformat(), 'end': end_day.isoformat(),
                'accounts': available, 'account': selected['key'] if selected else None,
                'first_observed_at': first, 'last_observed_at': last, 'days': points, 'buckets': points,
                'reset_events': events,
                'range_used_pp': (round(sum(p['used_pp'] or 0 for p in points), 6)
                                  if any(p['used_pp'] is not None for p in points) else None),
                'total_used_pp': round(total, 6) if total is not None else None,
                'before_range_pp': round(before, 6)}

    @staticmethod
    def reset_events(db, account_id, start_at, end_at, before):
        """Expose recognition times, without pretending polling saw the exact reset instant."""
        rows = db.execute('''WITH timeline AS (
            SELECT observed_at, used_percent, resets_at, reset,
                SUM(COALESCE(delta_pp,0)) OVER (
                    ORDER BY observed_at ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS increase
            FROM observations WHERE account_id=? AND observed_at>=? AND observed_at<?
        ) SELECT observed_at, used_percent, resets_at, increase
          FROM timeline WHERE reset=1 ORDER BY observed_at''', (account_id, start_at, end_at))
        return [{'observed_at': row['observed_at'], 'used_percent': row['used_percent'],
                 'next_reset_at': row['resets_at'], 'cumulative_pp': round(before + row['increase'], 6)}
                for row in rows]

    def query_hour(self, key=None, start=None, end=None):
        current = datetime.now(KST).replace(minute=0, second=0, microsecond=0)

        def parse(value, fallback):
            result = datetime.fromisoformat(value) if value else fallback
            result = result.replace(tzinfo=KST) if result.tzinfo is None else result.astimezone(KST)
            if result.minute or result.second or result.microsecond:
                raise ValueError('Hourly boundaries must fall on the hour')
            return result

        end_hour = parse(end, current)
        start_hour = parse(start, end_hour - timedelta(hours=23))
        count = int((end_hour - start_hour).total_seconds() / 3600) + 1
        if not 1 <= count <= MAX_HOURS or end_hour > current:
            raise ValueError('Choose 1 to 2160 hours ending no later than the current hour')
        points, events, before = [], [], 0
        with self.lock, self.connection() as db:
            # Keep metadata, all-time totals and hourly readings in one locked snapshot.
            result = self.query(key, current.date().isoformat(), current.date().isoformat())
            account = self.accounts.get(result['account'])
            if account:
                start_at = start_hour.timestamp()
                end_at = (end_hour + timedelta(hours=1)).timestamp()
                before = db.execute('SELECT COALESCE(SUM(delta_pp),0) FROM observations WHERE account_id=? AND observed_at<?',
                                    (account['id'], start_at)).fetchone()[0]
                rows = {row['bucket']: row for row in db.execute('''WITH readings AS (
                    SELECT *, CAST(observed_at / 3600 AS INTEGER) AS bucket,
                        ROW_NUMBER() OVER (PARTITION BY CAST(observed_at / 3600 AS INTEGER)
                                           ORDER BY observed_at DESC) AS newest
                    FROM observations WHERE account_id=? AND observed_at>=? AND observed_at<?
                ) SELECT bucket,
                        COUNT(*) AS observations, COUNT(delta_pp) AS intervals,
                        COALESCE(SUM(delta_pp),0) AS used_pp,
                        MAX(partial) AS partial, SUM(reset) AS resets,
                        MAX(CASE WHEN newest=1 THEN used_percent END) AS weekly_percent,
                        MAX(observed_at) AS weekly_observed_at
                    FROM readings
                    GROUP BY bucket ORDER BY bucket''', (account['id'], start_at, end_at))}
                cumulative = before
                for offset in range(count):
                    hour = start_hour + timedelta(hours=offset)
                    row = rows.get(int(hour.timestamp() // 3600))
                    if row:
                        cumulative += row['used_pp']
                    points.append({'date': hour.isoformat(timespec='minutes')[:16],
                                   'used_pp': round(row['used_pp'], 6) if row and row['intervals'] else None,
                                   'cumulative_pp': round(cumulative, 6) if row else None,
                                   'weekly_percent': row['weekly_percent'] if row else None,
                                   'weekly_observed_at': row['weekly_observed_at'] if row else None,
                                   'observations': row['observations'] if row else 0,
                                   'partial': bool(row['partial']) if row else False,
                                   'resets': row['resets'] if row else 0,
                                   'in_progress': hour == current})
                events = self.reset_events(db, account['id'], start_at, end_at, before)
        result.pop('days', None)
        result.update(granularity='hour', start=start_hour.isoformat(timespec='minutes')[:16],
                      end=end_hour.isoformat(timespec='minutes')[:16], hours=points, buckets=points,
                      reset_events=events,
                      before_range_pp=round(before, 6),
                      range_used_pp=(round(sum(p['used_pp'] or 0 for p in points), 6)
                                     if any(p['used_pp'] is not None for p in points) else None))
        return result
