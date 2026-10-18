"""Short-lived browser login sessions; official CLIs own all credential storage."""
import os
import re
import secrets
import select
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from token_tv.connect import login_command
from token_tv.sources import fetch_account, scoped_env

TERMINAL = {'succeeded', 'failed', 'cancelled', 'expired'}
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)')
CODE = re.compile(r'[A-Za-z0-9_-]{8,2048}(?:#[A-Za-z0-9_-]{8,256})?\Z')


def login_url(provider, value):
    """Only let official authorization pages become clickable links."""
    try:
        parsed = urlparse(value)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        if provider == 'claude' and parsed.hostname in {'claude.com', 'claude.ai', 'www.claude.com', 'www.claude.ai'}:
            return value if parsed.path in ('/cai/oauth/authorize', '/oauth/authorize') else None
        if provider == 'codex' and parsed.hostname == 'auth.openai.com' and parsed.path == '/codex/device':
            return value
    except ValueError:
        pass
    return None


@dataclass
class Login:
    account: dict
    lifetime: float
    id: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    phase: str = 'starting'
    message: str = '로그인 준비 중입니다.'
    created_at: float = field(default_factory=time.time)
    started: float = field(default_factory=time.monotonic)
    auth_url: str | None = None
    device_code: str | None = None
    process: object = None
    fd: int | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    input_lock: threading.Lock = field(default_factory=threading.Lock)
    thread: object = None


class LoginManager:
    def __init__(self, accounts, on_success=lambda: None, verify=fetch_account,
                 command=login_command, lifetime=900, account_locks=None):
        self.accounts = {a['key']: dict(a) for a in accounts}
        self.on_success, self.verify, self.command = on_success, verify, command
        self.lifetime = lifetime
        self.lock = threading.RLock()
        self.jobs = {}
        self.closed = False
        self.account_locks = account_locks or {key: threading.RLock() for key in self.accounts}

    def available(self, key):
        a = self.accounts.get(key, {})
        return (os.name == 'posix' and a.get('provider') in ('claude', 'codex')
                and bool(a.get('source_home')) and not any(a.get(k) for k in
                ('snapshot_file', 'codexbar_command', 'codexbar_url')))

    def _view(self, key):
        a = self.accounts[key]
        job = self.jobs.get(key)
        view = {'account': key, 'provider': a['provider'], 'alias': a['alias'], 'email': a['email'],
                'available': self.available(key), 'phase': 'idle'}
        if job:
            view.update(id=job.id, phase=job.phase, message=job.message,
                        expires_at=job.created_at + job.lifetime,
                        auth_url=job.auth_url, device_code=job.device_code)
        return view

    def snapshot(self, key):
        with self.lock:
            if key not in self.accounts:
                raise KeyError(key)
            return self._view(key)

    def active_keys(self):
        with self.lock:
            return {k for k, job in self.jobs.items() if job.phase not in TERMINAL}

    def start(self, key):
        with self.lock:
            if not self.available(key) or self.closed:
                raise ValueError('이 계정은 웹 로그인을 지원하지 않습니다.')
            old = self.jobs.get(key)
            # Also wait for a cancelled worker to release its CLI home.
            if old and (old.phase not in TERMINAL or old.thread.is_alive()):
                return self._view(key)
            job = Login(self.accounts[key], self.lifetime)
            self.jobs[key] = job
            job.thread = threading.Thread(target=self._run, args=(job,), daemon=True)
            job.thread.start()
            return self._view(key)

    def _finish(self, job, phase, message):
        with self.lock:
            if job.phase in TERMINAL:
                return False
            job.phase, job.message = phase, message
            job.auth_url = job.device_code = None
            return True

    def submit(self, key, identifier, code):
        if not isinstance(code, str) or not CODE.fullmatch(code):
            raise ValueError('공식 로그인 화면에서 받은 인증 코드를 입력해 주세요.')
        with self.lock:
            job = self.jobs.get(key)
            if not job or job.id != identifier or job.phase != 'waiting' or job.account['provider'] != 'claude':
                raise ValueError('입력 가능한 로그인 세션이 아닙니다. 다시 시작해 주세요.')
            if time.monotonic() - job.started >= job.lifetime:
                raise ValueError('로그인 시간이 만료되었습니다.')
            with job.input_lock:
                if job.fd is None:
                    raise ValueError('로그인 창이 닫혔습니다. 다시 시작해 주세요.')
                os.write(job.fd, (code + '\n').encode())
            job.phase, job.message = 'verifying', '인증 및 계정 확인 중입니다.'
            job.auth_url = None
            return self._view(key)

    def cancel(self, key, identifier):
        with self.lock:
            job = self.jobs.get(key)
            if not job or job.id != identifier:
                raise ValueError('로그인 세션이 바뀌었습니다.')
            job.stop.set()
            self._finish(job, 'cancelled', '로그인을 취소했습니다.')
            return self._view(key)

    def _read_prompt(self, job, text):
        with self.lock:
            if job.phase not in ('starting', 'waiting') or job.stop.is_set():
                return
            for candidate in re.findall(r'https://[^\s<>"\x1b]+(?=\s)', ANSI.sub('', text)):
                url = login_url(job.account['provider'], candidate)
                if url:
                    job.auth_url = url
            if job.account['provider'] == 'codex':
                codes = re.findall(r'\b[A-Z0-9]{4,6}-[A-Z0-9]{4,6}\b', ANSI.sub('', text))
                if codes:
                    job.device_code = codes[-1]
            if job.auth_url and (job.account['provider'] == 'claude' or job.device_code):
                job.phase = 'waiting'
                job.message = '공식 로그인 페이지에서 인증을 완료해 주세요.'

    def _run(self, job):
        # Polling, token rotation and browser login must not share a CLI home concurrently.
        lock = self.account_locks[job.account['key']]
        while not lock.acquire(timeout=.2):
            if job.stop.is_set():
                return
            if time.monotonic() - job.started >= job.lifetime:
                self._finish(job, 'expired', '로그인 대기 시간이 만료되었습니다. 다시 시도해 주세요.')
                return
        try:
            self._run_locked(job)
        finally:
            lock.release()

    def _run_locked(self, job):
        master = slave = None
        process = None
        # Output is transient and private; only allowlisted fields leave this worker.
        output = ''
        try:
            import pty
            import termios
            home = Path(job.account['source_home']).expanduser()
            home.mkdir(parents=True, exist_ok=True)
            home.chmod(0o700)
            master, slave = pty.openpty()
            attrs = termios.tcgetattr(slave)
            attrs[3] &= ~termios.ECHO
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
            env = scoped_env(job.account['provider'], home)
            env.update(TERM='xterm-256color', BROWSER='true', NO_COLOR='1')
            if job.stop.is_set():
                return
            process = subprocess.Popen(self.command(job.account), env=env, stdin=slave,
                                       stdout=slave, stderr=slave, start_new_session=True)
            os.close(slave)
            slave = None
            with self.lock:
                job.process, job.fd = process, master
            while True:
                if job.stop.is_set():
                    return
                if time.monotonic() - job.started >= job.lifetime:
                    self._finish(job, 'expired', '로그인 시간이 만료되었습니다. 다시 시작해 주세요.')
                    return
                ready, _, _ = select.select([master], [], [], .15)
                if ready:
                    try:
                        chunk = os.read(master, 8192)
                    except OSError:
                        chunk = b''
                    if chunk:
                        output = (output + chunk.decode('utf-8', errors='replace'))[-16384:]
                        self._read_prompt(job, output)
                if process.poll() is not None:
                    break
            if process.returncode != 0:
                message = '로그인이 완료되지 않았습니다. 다시 시도해 주세요.'
                if 'device' in output.lower() and any(w in output.lower() for w in ('disabled', 'not enabled')):
                    message = 'ChatGPT 보안 설정에서 기기 코드 로그인을 허용한 뒤 다시 시도해 주세요.'
                self._finish(job, 'failed', message)
                return
            with self.lock:
                if job.phase in TERMINAL:
                    return
                job.phase, job.message = 'verifying', '선택한 계정과 사용량을 확인하고 있습니다.'
                job.auth_url = job.device_code = None
            # Never let a linked snapshot mask the identity of the new CLI login.
            direct = {k: v for k, v in job.account.items() if k not in ('snapshot_file', 'fallback_snapshot_file')}
            row = self.verify(direct)
            if row.get('status') in ('ok', 'quota_unavailable') and row.get('identity_verified') is True:
                if self._finish(job, 'succeeded', '로그인 완료. 사용량과 시계 화면을 갱신합니다.'):
                    self.on_success()
            else:
                self._finish(job, 'failed', '계정 이메일이 일치하지 않거나 사용량을 확인하지 못했습니다. 선택한 계정으로 다시 로그인해 주세요.')
        except Exception:
            # Exceptions and CLI text may contain auth material: never log or return them.
            self._finish(job, 'failed', '로그인 프로그램을 실행하거나 인증을 확인하지 못했습니다. 다시 시도해 주세요.')
        finally:
            output = ''
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    process.wait(timeout=2)
            with job.input_lock:
                for fd in (master, slave):
                    if fd is not None:
                        os.close(fd)
                job.fd = None

    def close(self):
        with self.lock:
            self.closed = True
            jobs = list(self.jobs.values())
            for job in jobs:
                job.stop.set()
                self._finish(job, 'cancelled', '서버가 종료되어 로그인을 취소했습니다.')
        for job in jobs:
            job.thread.join(timeout=3)
