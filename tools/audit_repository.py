# SPDX-License-Identifier: GPL-3.0-only
"""Check source provenance, syntax and common accidental disclosure patterns."""
import ast,hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
IGNORED={'.git','__pycache__','.venv','build','dist','work'}
FORBIDDEN={'.exe','.dll','.sys','.zip','.png','.webp','.jpg','.jpeg','.ico','.ttf','.woff','.woff2',
           '.pyc','.pem','.key','.pfx','.p12','.dpapi','.pcap','.pcapng','.etl','.dmp','.log'}
PATTERNS={
 'private-key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
 'github-token':re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,})\b'),
 'aws-key':re.compile(r'\bAKIA[A-Z0-9]{16}\b'),
 'private-profile-path':re.compile(r'[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+',re.I),
}
def inspect():
    issues=[];files=[];syntax=0
    resources=json.loads((ROOT/'metadata/included-resources.json').read_text(encoding='utf-8'))['files']
    included={entry['path']:entry for entry in resources}
    if len(included)!=len(resources):issues.append({'path':'metadata/included-resources.json','issue':'duplicate-resource'})
    for name,entry in included.items():
        p=ROOT/name
        allowed=(name.startswith('src/imperio_aion/assets/') or name in ('src/updater/logo.png','src/updater/imperioaion.ico'))
        if not allowed or '..' in Path(name).parts or Path(name).suffix.lower() not in {'.png','.webp','.ico','.svg'}:
            issues.append({'path':name,'issue':'invalid-resource-path'});continue
        if not p.is_file() or p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=entry['sha256']:
            issues.append({'path':name,'issue':'resource-integrity'})
    for p in sorted(ROOT.rglob('*')):
        rel=p.relative_to(ROOT)
        if any(part in IGNORED for part in rel.parts):continue
        if p.is_symlink():issues.append({'path':rel.as_posix(),'issue':'symlink'});continue
        if not p.is_file():continue
        files.append(p)
        if rel.as_posix() in included and p.suffix.lower() in {'.png','.webp','.ico'}:
            continue  # Only explicitly inventoried images bypass text decoding.
        if p.suffix.lower() in FORBIDDEN or p.name.startswith('.env'):
            issues.append({'path':rel.as_posix(),'issue':'unexpected-binary-or-private-file'})
        try:text=p.read_text(encoding='utf-8-sig')
        except UnicodeError:issues.append({'path':rel.as_posix(),'issue':'non-text'});continue
        # Third-party license texts and metadata may discuss keys, never contain one.
        for kind,pattern in PATTERNS.items():
            if pattern.search(text):issues.append({'path':rel.as_posix(),'issue':kind})
        if p.suffix=='.py':
            try:ast.parse(text,filename=rel.as_posix());syntax+=1
            except SyntaxError as e:issues.append({'path':rel.as_posix(),'issue':'syntax','line':e.lineno})
    manifest=json.loads((ROOT/'metadata/source-map.json').read_text(encoding='utf-8'))
    mapped={entry['path'] for entry in manifest['files']}
    for entry in manifest['files']:
        p=ROOT/entry['path']
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=entry['prepared_sha256']:
            issues.append({'path':entry['path'],'issue':'source-integrity'})
    for p in files:
        rel=p.relative_to(ROOT).as_posix()
        if rel.startswith('src/') and rel not in mapped and rel not in included:issues.append({'path':rel,'issue':'unmapped-source'})
    gpl=(ROOT/'LICENSE').read_text(encoding='utf-8')
    if 'Version 3, 29 June 2007' not in gpl or 'END OF TERMS AND CONDITIONS' not in gpl:
        issues.append({'path':'LICENSE','issue':'missing-gpl3'})
    return {'status':'PASS' if not issues else 'FAIL','files_checked':len(files),'python_syntax_checked':syntax,
      'mapped_sources':len(mapped),'inventoried_resources':len(included),'issues':issues,
      'scope':'pattern scan and manifest checks; not a complete security audit or resource license clearance'}
if __name__=='__main__':
    result=inspect();print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(bool(result['issues']))
