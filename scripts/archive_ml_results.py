"""Read-only ML result preservation. No application imports, DB or backtest calls.

prepare/verify work with the connected Drive tools; upload uses external OAuth.
All archives are deterministic and include a per-original-file SHA-256 manifest.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / 'artifacts/ml/generated'
DESTINATION = '1d_z78QpkOZoEmDyXydgwA82zmyezX5Vd'
MODES = ('moving_average', 'moving_average_zero_cost', 'buy_and_hold', 'cash')
BACKTEST_FILES = {'report.json', 'summary.md', 'input.json'} | {
    f'{mode}-{kind}.csv' for mode in MODES for kind in ('daily', 'equity')}
SENSITIVE_KEY = re.compile(r'(password|passwd|secret|token|api.?key|private.?key|account|acct|authorization|credential|user.?id|owner|pin$|계좌|비밀번호|인증키|접근토큰|주민등록)', re.I)
SENSITIVE_TEXT = re.compile(
    r'-----BEGIN .*PRIVATE KEY-----|Bearer\s+\S+|(?:postgres(?:ql)?|mysql)://|'
    r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|'
    r'\b(?:api[_-]?key|password|passwd|secret|access[_-]?token|refresh[_-]?token|pin|account[_-]?(?:id|no|number))\s*[=:]\s*["\']?[^\s,"\'}]+|'
    r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\beyJ[\w-]+\.[\w-]+\.[\w-]+\b|'
    r'(?:계좌번호|비밀번호|인증키|접근토큰|주민등록번호)\s*[=:]\s*\S+', re.I)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')


def scan(name, data):
    text = data.decode('utf-8-sig', errors='strict')
    if SENSITIVE_TEXT.search(text):
        raise ValueError(f'Sensitive content blocked: {name}')
    if name.endswith('.json'):
        def walk(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if SENSITIVE_KEY.search(key):
                        raise ValueError(f'Sensitive JSON field blocked: {name}')
                    walk(value)
            elif isinstance(obj, list):
                for value in obj:
                    walk(value)
        walk(json.loads(text))


def checked_source(path):
    path = Path(path).absolute()
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(GENERATED.resolve()) or resolved != path:
        raise ValueError('Source must be a non-linked path under artifacts/ml/generated')
    if not resolved.is_dir():
        raise ValueError('Source must be a directory')
    return resolved


def bundle(day, source):
    source = checked_source(source)
    compact = day.replace('-', '')
    if not re.fullmatch(rf'(backtest|daily-review)-{compact}-[A-Za-z0-9-]+', source.name):
        raise ValueError('Source run directory does not match requested date')
    is_backtest = source.name.startswith('backtest-')
    expected = BACKTEST_FILES if is_backtest else {f'quality-{day}.json'}
    actual = {p.name for p in source.iterdir()}
    if actual != expected:
        raise ValueError(f'Unexpected or missing files in {source.name}; refusing whole run')
    payloads = {}
    for name in sorted(expected):
        path = source / name
        if path.is_symlink() or path.resolve() != path or not path.is_file():
            raise ValueError('Linked or non-file artifact blocked')
        data = path.read_bytes()
        scan(name, data)
        payloads[name] = data
    report = json.loads(payloads['report.json' if is_backtest else f'quality-{day}.json'])
    if is_backtest:
        if report.get('strategy_id') != 17 or report.get('config', {}).get('execution') != 'next_open':
            raise ValueError('Unexpected strategy or execution')
        if not report.get('observed_days') or max(report['observed_days']) != day:
            raise ValueError('Backtest end date mismatch')
    elif report.get('report_date') != day:
        raise ValueError('Quality date mismatch')
    manifest = {'version': 1, 'date': day, 'run': source.name,
                'report_run_id': report.get('run_id'),
                'files': [{'name': name, 'size': len(data), 'sha256': sha(data)}
                          for name, data in payloads.items()]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in {**payloads, 'manifest.json': canonical(manifest)}.items():
            info = zipfile.ZipInfo(f'{day}/{source.name}/{name}', (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, data, compresslevel=9)
    data = buf.getvalue()
    digest = sha(data)
    return {'date': day, 'run': source.name, 'source': str(source), 'sha256': digest,
            'name': f'{day}--{source.name}--{digest}.zip', 'size': len(data),
            'manifest': manifest}, data


def prepare(day, sources):
    date.fromisoformat(day)
    if len(set(map(str, sources))) != len(sources) or not sources:
        raise ValueError('Explicit unique source directories required')
    # Scan every source before writing anything eligible for external upload.
    bundles = [bundle(day, source) for source in sources]
    output = GENERATED / 'drive-archive' / day
    output.mkdir(parents=True, exist_ok=True)
    plan = {'version': 1, 'folder_id': DESTINATION, 'date': day, 'archives': []}
    for item, data in bundles:
        path = output / item['name']
        if path.exists() and path.read_bytes() != data:
            raise ValueError('Archive path collision')
        path.write_bytes(data)
        plan['archives'].append({**item, 'path': str(path)})
    path = output / 'plan.json'
    path.write_bytes(canonical(plan))
    return path, plan


def verify(item, data):
    checked_source(item['source'])
    if sha(data) != item['sha256'] or len(data) != item['size']:
        raise ValueError('Downloaded archive SHA-256/size mismatch')
    prefix = f"{item['date']}/{item['run']}/"
    expected = item['manifest']
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [prefix + x['name'] for x in expected['files']] + [prefix + 'manifest.json']
        if len(archive.namelist()) != len(names) or set(archive.namelist()) != set(names):
            raise ValueError('Archive members mismatch')
        if json.loads(archive.read(prefix + 'manifest.json')) != expected:
            raise ValueError('Manifest mismatch')
        for entry in expected['files']:
            content = archive.read(prefix + entry['name'])
            scan(entry['name'], content)
            if len(content) != entry['size'] or sha(content) != entry['sha256']:
                raise ValueError('Original file SHA-256 mismatch')
            if content != (Path(item['source']) / entry['name']).read_bytes():
                raise ValueError('Original file changed since prepare')
    return len(expected['files'])


class Drive:
    """Minimal Drive v3 transport. Never log credentials or HTTP response bodies."""
    def __init__(self):
        token = os.environ.get('FOLIO_DRIVE_ACCESS_TOKEN')
        if not token:
            auth_path = Path(os.environ['FOLIO_DRIVE_OAUTH_FILE']).resolve(strict=True)
            if auth_path.is_relative_to(ROOT):
                raise ValueError('OAuth file must be outside repository')
            auth = json.loads(auth_path.read_text(encoding='utf-8'))
            body = urllib.parse.urlencode({k: auth[k] for k in ('client_id', 'client_secret', 'refresh_token')} |
                                         {'grant_type': 'refresh_token'}).encode()
            request = urllib.request.Request('https://oauth2.googleapis.com/token', data=body)
            with urllib.request.urlopen(request, timeout=60) as response:
                token = json.load(response)['access_token']
        self.token = token

    def request(self, path, query=None, data=None, content_type=None, upload=False, raw=False):
        base = 'https://www.googleapis.com/' + ('upload/' if upload else '') + 'drive/v3/'
        query = {'supportsAllDrives': 'true', **(query or {})}
        headers = {'Authorization': 'Bearer ' + self.token}
        if content_type:
            headers['Content-Type'] = content_type
        req = urllib.request.Request(base + path + '?' + urllib.parse.urlencode(query), data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.read() if raw else json.load(response)

    def matches(self, name):
        query = f"'{DESTINATION}' in parents and trashed = false and name = '{name}'"
        found, page = [], None
        while True:
            args = {'q': query, 'fields': 'files(id,name,webViewLink),nextPageToken',
                    'includeItemsFromAllDrives': 'true', 'pageSize': '100'}
            if page:
                args['pageToken'] = page
            result = self.request('files', args)
            found.extend(result['files'])
            page = result.get('nextPageToken')
            if not page:
                return found

    def create(self, item, data):
        boundary = 'folio_' + uuid.uuid4().hex
        metadata = canonical({'name': item['name'], 'parents': [DESTINATION],
                              'mimeType': 'application/zip', 'appProperties': {'sha256': item['sha256']}})
        body = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'.encode() + metadata +
                f'\r\n--{boundary}\r\nContent-Type: application/zip\r\n\r\n'.encode() + data +
                f'\r\n--{boundary}--\r\n'.encode())
        return self.request('files', {'uploadType': 'multipart', 'fields': 'id,name,webViewLink'},
                            body, 'multipart/related; boundary=' + boundary, upload=True)


def upload(plan, drive):
    # Read-back is required even for a deduplicated candidate. Never trust its name alone.
    receipts = []
    for item in plan['archives']:
        data = Path(item['path']).read_bytes()
        verify(item, data)
        matches = drive.matches(item['name'])
        if len(matches) > 1:
            raise ValueError('Multiple remote matches; manual review required')
        remote = matches[0] if matches else drive.create(item, data)
        downloaded = drive.request('files/' + remote['id'], {'alt': 'media'}, raw=True)
        count = verify(item, downloaded)
        receipts.append({'run': item['run'], 'file_id': remote['id'], 'url': remote.get('webViewLink'),
                         'sha256': item['sha256'], 'verified_files': count, 'reused': bool(matches)})
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--date', required=True)
    prep.add_argument('--source', action='append', required=True)
    for command in ('verify', 'upload'):
        child = sub.add_parser(command)
        child.add_argument('--plan', required=True)
        if command == 'verify':
            child.add_argument('--run', required=True)
            child.add_argument('--download', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            path, plan = prepare(args.date, args.source)
            print(json.dumps({'plan': str(path), 'archives': len(plan['archives'])}))
        else:
            plan = json.loads(Path(args.plan).read_text(encoding='utf-8'))
            if plan['folder_id'] != DESTINATION:
                raise ValueError('Unexpected Drive destination')
            if args.command == 'verify':
                item = next(x for x in plan['archives'] if x['run'] == args.run)
                count = verify(item, Path(args.download).read_bytes())
                print(json.dumps({'run': args.run, 'verified_files': count, 'sha256': item['sha256']}))
            else:
                # Exclusive local lock prevents same-machine overlapping upload races.
                lock = Path(args.plan).with_suffix('.upload.lock')
                lock_handle = lock.open('x')
                try:
                    with lock_handle:
                        receipts = upload(plan, Drive())
                        Path(args.plan).with_name('upload-receipt.json').write_bytes(canonical(receipts))
                        print(json.dumps(receipts))
                finally:
                    lock.unlink()
    except Exception as exc:
        # No exception body: OAuth / HTTP errors may contain secrets.
        print(json.dumps({'archive_status': 'FAILED', 'error_type': type(exc).__name__,
                          'local_originals_retained': True}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
