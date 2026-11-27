"""Package only the standalone file/report; record artifact/evidence hashes."""
from pathlib import Path
import hashlib, json, zipfile, difflib

root=Path(__file__).resolve().parent
original=(root/'reference/folio-ux02-original.html').read_text(encoding='utf-8')
final=(root/'folio-ux02-pc.html').read_text(encoding='utf-8')
(root/'evidence/source-change.diff').write_text(''.join(difflib.unified_diff(original.splitlines(True),final.splitlines(True),fromfile='provided-original.html',tofile='folio-ux02-pc.html')),encoding='utf-8',newline='\n')
with zipfile.ZipFile(root/'FOLIO-UX02-PC-preview.zip','w',zipfile.ZIP_DEFLATED) as archive:
    for name in ['folio-ux02-pc.html','VALIDATION.md']:
        info=zipfile.ZipInfo(name,date_time=(2026,11,11,0,0,0))
        info.compress_type=zipfile.ZIP_DEFLATED
        archive.writestr(info,(root/name).read_bytes())
with zipfile.ZipFile(root/'FOLIO-UX02-PC-preview.zip') as archive:
    assert archive.testzip() is None
    for name in archive.namelist():
        assert archive.read(name)==(root/name).read_bytes()
files=[root/'folio-ux02-pc.html',root/'FOLIO-UX02-PC-preview.zip',root/'VALIDATION.md',*sorted((root/'evidence').glob('*.png'))]
manifest={str(p.relative_to(root)).replace('\\','/'):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in files}
(root/'evidence/artifact-hashes.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('ZIP integrity and extracted bytes PASS; artifact/screenshot hashes recorded')
