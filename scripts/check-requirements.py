"""No network or application imports: install only when pinned runtime packages differ."""
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def requirements_ready(path):
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, expected = line.split('==', 1)
        try:
            if version(name.split('[', 1)[0]) != expected:
                return False
        except PackageNotFoundError:
            return False
    # Optional distributions requested by our extras must also be present.
    try:
        for name in ('psycopg-binary', 'httptools', 'watchfiles', 'python-dotenv', 'PyYAML'):
            version(name)
    except PackageNotFoundError:
        return False
    return True


if __name__ == '__main__':
    raise SystemExit(0 if requirements_ready(Path(__file__).resolve().parent.parent / 'requirements.txt') else 1)
