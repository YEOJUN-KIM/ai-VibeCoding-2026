"""Disposable guarded runner for a few *existing* unittest modules (verification utility).

Purpose: re-run the pre-existing MA/PAPER/backtest regression modules that never need a real
database, while making accidental DB/network/secret access fail loudly. It is NOT a test module
(not collected by discovery), starts no server/worker, and does not stub the engine code under test.

    python -B tests/day01_safe_regression_runner.py --selftest
    python -B tests/day01_safe_regression_runner.py tests.test_ml_backtest tests.test_strategy_engine \
        tests.test_paper_management tests.test_paper_feed

Guards (Python level, installed before any project import):
  * sensitive environment variables (POSTGRES_*, TOSS_*, DART_*, OPENAI_*, ANTHROPIC_*, AWS_*,
    *_API_KEY/_SECRET/_TOKEN/_PASSWORD) => REFUSE to run. Only variable NAMES are ever printed.
  * `.env` files: an existence probe (os.path/Path.exists, used by settings) is allowed and the runner
    itself refuses if `<root>/.env` exists; any attempt to OPEN a file whose name starts with `.env`
    is blocked before the OS is touched, so contents can never be read.
  * `.paper-history` paths cannot be opened.
  * network: every socket connect/connect_ex/bind/listen/accept, create_connection, getaddrinfo and
    gethostbyname is blocked, EXCEPT inside CPython's `socket._fallback_socketpair` frame (what asyncio
    uses on Windows for its self-pipe): loopback only, ephemeral port, no external peer.
  * psycopg: connect APIs (psycopg.connect, Connection/AsyncConnection.connect, pq PGconn connect
    entry points, pool classes if importable) raise AssertionError.
Limit: guards are in-process monkeypatches, not an OS sandbox; a C extension opening its own socket
without those Python entry points is not intercepted (the psycopg entry points above are).
"""
import builtins
import io
import os
import pathlib
import socket
import sys
import unittest

EXCLUDED = {
    'tests.test_strategy_engine.StrategyEngineTests.test_journal_survives_reset_and_contains_costs_reason_and_owner':
        'writes a journal file under .paper-history (protected local execution record path)',
}
SENSITIVE_PREFIXES = ('POSTGRES_', 'PG', 'TOSS_', 'DART_', 'OPENAI_', 'ANTHROPIC_', 'AWS_', 'DATABASE_')
SENSITIVE_SUFFIXES = ('_API_KEY', '_SECRET', '_TOKEN', '_PASSWORD', '_PASSWD')


class Blocked(AssertionError):
    pass


def _blocked(*_a, **_k):
    raise Blocked('network/DB access blocked by day01_safe_regression_runner')


def _in_socketpair_fallback():
    frame = sys._getframe(2)
    while frame is not None:
        if frame.f_code.co_name == '_fallback_socketpair' and frame.f_code.co_filename.endswith('socket.py'):
            return True
        frame = frame.f_back
    return False


def install_guards(root):
    root = pathlib.Path(root).resolve()
    hits = sorted(k for k in os.environ
                  if k.upper().startswith(SENSITIVE_PREFIXES) or k.upper().endswith(SENSITIVE_SUFFIXES))
    if hits:
        sys.exit('refusing to run: sensitive environment variable names present: ' + ', '.join(hits))
    if os.path.exists(root / '.env'):
        sys.exit('refusing to run: .env exists in the worktree (not opened)')
    os.environ['APP_MODE'] = 'PAPER'

    real = {n: getattr(socket.socket, n) for n in ('connect', 'bind', 'listen', 'accept', 'connect_ex')}

    def loopback(name):
        def guarded(self, *a, **k):
            if _in_socketpair_fallback():
                if name in ('connect', 'bind'):
                    addr = a[0] if a else None
                    if not (isinstance(addr, tuple) and addr[0] in ('127.0.0.1', '::1')):
                        _blocked()
                return real[name](self, *a, **k)
            _blocked()
        return guarded

    for name in real:
        setattr(socket.socket, name, loopback(name))
    for name in ('create_connection', 'getaddrinfo', 'gethostbyname', 'gethostbyname_ex', 'create_server'):
        if hasattr(socket, name):
            setattr(socket, name, _blocked)

    import psycopg
    psycopg.connect = _blocked
    for cls_name in ('Connection', 'AsyncConnection'):
        cls = getattr(psycopg, cls_name, None)
        if cls is not None:
            cls.connect = classmethod(_blocked)
    try:
        from psycopg import pq
        for cls_name in ('PGconn',):
            cls = getattr(pq, cls_name, None)
            for attr in ('connect', 'connect_start'):
                if cls is not None and hasattr(cls, attr):
                    try:
                        setattr(cls, attr, staticmethod(_blocked))
                    except (TypeError, AttributeError):
                        pass  # immutable extension type: unreachable without psycopg.connect, which is blocked
    except Exception:
        pass
    try:
        import psycopg_pool
        for cls_name in ('ConnectionPool', 'AsyncConnectionPool', 'NullConnectionPool'):
            cls = getattr(psycopg_pool, cls_name, None)
            if cls is not None:
                cls.__init__ = _blocked
    except ImportError:
        pass

    original_open = builtins.open

    def guarded_open(file, mode='r', *a, **k):
        if not isinstance(file, int):
            text = str(file).replace('\\', '/')
            if pathlib.PurePosixPath(text).name.startswith('.env'):
                raise Blocked('.env* open blocked (existence probes are allowed, reads are not)')
            if '.paper-history' in text:
                raise Blocked('.paper-history open blocked')
        return original_open(file, mode, *a, **k)

    builtins.open = guarded_open
    io.open = guarded_open


def selftest(root):
    import psycopg
    checks = []

    def must_block(label, fn):
        try:
            fn()
        except Blocked:
            checks.append((label, True))
        else:
            checks.append((label, False))

    must_block('psycopg.connect', lambda: psycopg.connect('host=127.0.0.1 port=1'))
    must_block('Connection.connect', lambda: psycopg.Connection.connect('host=127.0.0.1 port=1'))
    must_block('open .env', lambda: open(os.path.join(root, '.env')))
    must_block('open .env.example', lambda: open(os.path.join(root, '.env.example')))
    must_block('open .paper-history', lambda: open(os.path.join(root, '.paper-history', 'x.jsonl'), 'w'))
    s = socket.socket()
    must_block('socket.connect 127.0.0.1:5432', lambda: s.connect(('127.0.0.1', 5432)))
    must_block('socket.bind', lambda: s.bind(('127.0.0.1', 0)))
    must_block('socket.listen', lambda: s.listen())
    must_block('create_connection', lambda: socket.create_connection(('example.com', 80)))
    must_block('getaddrinfo', lambda: socket.getaddrinfo('example.com', 80))
    s.close()
    import asyncio
    loop = asyncio.new_event_loop()          # needs the allowed socketpair fallback only
    loop.close()
    checks.append(('asyncio loop creation (socketpair fallback allowed)', True))
    for label, ok in checks:
        print(('PASS ' if ok else 'FAIL ') + label)
    return all(ok for _, ok in checks)


def main(argv):
    root = pathlib.Path(__file__).resolve().parents[1]
    mode_selftest = argv[:1] == ['--selftest']
    modules = [] if mode_selftest else argv
    install_guards(root)
    sys.path.insert(0, str(root))
    os.chdir(root)
    if mode_selftest:
        return 0 if selftest(root) else 1
    if not modules:
        sys.exit('usage: day01_safe_regression_runner.py --selftest | <unittest module> ...')

    def flatten(s):
        for t in s:
            yield from flatten(t) if isinstance(t, unittest.TestSuite) else [t]

    suite, skipped = unittest.TestSuite(), []
    for m in modules:
        for t in flatten(unittest.defaultTestLoader.loadTestsFromName(m)):
            if t.id() in EXCLUDED:
                skipped.append(t.id())
            else:
                suite.addTest(t)
    for tid in skipped:
        print('EXCLUDED:', tid, '--', EXCLUDED[tid])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    print(f'RAN {result.testsRun} FAIL {len(result.failures)} ERR {len(result.errors)} '
          f'SKIP {len(result.skipped)} EXCLUDED {len(skipped)}')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
