"""Create a private PostgreSQL backup and optionally verify it in a temporary DB."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import tempfile
import socket

import psycopg
from psycopg import sql
from auto_trader.settings import settings


def fingerprint(conn):
    result = {}
    tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
    for (name,) in tables:
        rows = conn.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} t').format(sql.Identifier('public', name))).fetchall()
        canonical = sorted(json.dumps(json.loads(row[0]), sort_keys=True, ensure_ascii=False) for row in rows)
        result[name] = {'rows': len(rows), 'sha256': hashlib.sha256('\n'.join(canonical).encode()).hexdigest()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    binaries = Path(r'C:\Program Files\PostgreSQL\18\bin')
    def executable(name):
        found = shutil.which(name) or str(binaries / (name + '.exe'))
        if not Path(found).is_file():
            raise RuntimeError(f'{name} not found')
        return found
    connection = dict(host=settings.postgres_host, port=settings.postgres_port,
                      user=settings.postgres_user, password=settings.postgres_password)
    env = os.environ.copy()
    env['PGPASSWORD'] = settings.postgres_password
    options = ['-h', settings.postgres_host, '-p', str(settings.postgres_port), '-U', settings.postgres_user, '--no-password']
    directory = Path(__file__).resolve().parent / '.backups'
    directory.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    target = directory / f'postgres-{stamp}-{uuid.uuid4().hex[:8]}.dump'
    # Export a consistent snapshot shared by the dump and source comparison.
    with psycopg.connect(**connection, dbname=settings.postgres_db) as source:
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        snapshot = source.execute('SELECT pg_export_snapshot()').fetchone()[0]
        expected = fingerprint(source) if args.verify else None
        subprocess.run([executable('pg_dump'), *options, '-d', settings.postgres_db,
                        '-Fc', '--snapshot', snapshot, '-f', str(target)], env=env, check=True, capture_output=True)
    report = {'backup': target.name, 'bytes': target.stat().st_size,
              'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'verified': False}
    if args.verify:
        # A separate local PostgreSQL instance avoids requiring CREATEDB on the live account.
        with tempfile.TemporaryDirectory(prefix='restore-check-', dir=directory) as root:
            cluster = Path(root) / 'data'
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            hidden = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            subprocess.run([executable('initdb'), '-D', str(cluster), '-U', 'restore_check', '--auth=trust', '--encoding=UTF8'], check=True, capture_output=True, **hidden)
            subprocess.run([executable('pg_ctl'), '-D', str(cluster), '-l', str(Path(root) / 'postgres.log'), '-o', f'-h 127.0.0.1 -p {port}', '-w', 'start'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **hidden)
            try:
                restore_options = ['-h', '127.0.0.1', '-p', str(port), '-U', 'restore_check', '--no-password']
                subprocess.run([executable('pg_restore'), *restore_options, '-d', 'postgres',
                                '--exit-on-error', '--no-owner', '--no-privileges', str(target)], check=True, capture_output=True, **hidden)
                with psycopg.connect(host='127.0.0.1', port=port, user='restore_check', dbname='postgres') as restored:
                    actual = fingerprint(restored)
                if actual != expected:
                    raise RuntimeError('Restored table contents differ from backup snapshot')
                report.update(verified=True, tables=actual)
            finally:
                subprocess.run([executable('pg_ctl'), '-D', str(cluster), '-m', 'fast', '-w', 'stop'], check=True, capture_output=True, **hidden)
    target.with_suffix('.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'backup': str(target), 'bytes': report['bytes'], 'verified': report['verified'],
                      'tables': {k:v['rows'] for k,v in report.get('tables',{}).items()}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
