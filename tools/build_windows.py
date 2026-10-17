# SPDX-License-Identifier: GPL-3.0-only
"""Build unsigned Windows artifacts from inventoried source. Never install/sign.

Run with the locked CPython 3.14 x64 environment. Network is limited to pinned
upstream WinDivert downloads. No private keys or live application state is read.
"""
import argparse,hashlib,importlib.metadata,json,os,shutil,subprocess,sys,urllib.request,zipfile
from pathlib import Path
from build_native import compile_helper
ROOT=Path(__file__).resolve().parents[1]
VERSION='0.8.71'
VENDOR={
 'binary.zip':('https://github.com/basil00/WinDivert/releases/download/v2.2.2/WinDivert-2.2.2-A.zip','63cb41763bb4b20f600b6de04e991a9c2be73279e317d4d82f237b150c5f3f15'),
 'source.zip':('https://api.github.com/repos/basil00/WinDivert/zipball/v2.2.2','366f83f7243e3bb4972a25af2329c6cbd761bba215a00f9f38d9d45603d9d51d')}
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def write_json(p,value):p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
def inputs():
    ignored={'.git','__pycache__','.venv','build','dist','work'}
    return {p.relative_to(ROOT).as_posix():sha(p) for p in sorted(ROOT.rglob('*'))
      if p.is_file() and not any(part in ignored for part in p.relative_to(ROOT).parts)}
def vendor(cache,destination):
    cache.mkdir(parents=True,exist_ok=True);destination.mkdir(parents=True)
    for name,(url,expected) in VENDOR.items():
        target=cache/name
        if not target.is_file():
            request=urllib.request.Request(url,headers={'User-Agent':'Imperio-Aion-build'})
            with urllib.request.urlopen(request,timeout=60) as response:raw=response.read(8*1024*1024)
            if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Upstream hash mismatch: '+name)
            target.write_bytes(raw)
        if sha(target)!=expected:raise ValueError('Cached upstream hash mismatch: '+name)
    with zipfile.ZipFile(cache/'binary.zip') as archive:
        for name,expected in [('WinDivert.dll','c1e060ee19444a259b2162f8af0f3fe8c4428a1c6f694dce20de194ac8d7d9a2'),('WinDivert64.sys','8da085332782708d8767bcace5327a6ec7283c17cfb85e40b03cd2323a90ddc2')]:
            raw=archive.read('WinDivert-2.2.2-A/x64/'+name)
            if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Vendor binary hash mismatch')
            (destination/name).write_bytes(raw)
    shutil.copy2(cache/'source.zip',destination/'WinDivert-2.2.2-source.zip')
    with zipfile.ZipFile(cache/'source.zip') as archive:
        license_name=next(n for n in archive.namelist() if n.endswith('/LICENSE'))
        (destination/'LICENSE.txt').write_bytes(archive.read(license_name))
    (destination/'NOTICE.txt').write_text('WinDivert 2.2.2, basil00 and contributors. Redistributed unmodified under LGPL-3.0.\nCorresponding source and license included. https://github.com/basil00/WinDivert\nDynamically loaded; users may replace the library with a compatible modified version and debug it.\n',encoding='utf8')
def version_file(target,filename,description):
    fields={'CompanyName':'Jose Mardegam','FileDescription':description,'FileVersion':VERSION,
      'OriginalFilename':filename,'ProductName':'Império Aion','ProductVersion':VERSION}
    items=','.join('StringStruct('+repr(k)+','+repr(v)+')' for k,v in fields.items())
    numbers=tuple(map(int,VERSION.split('.')))+(0,)
    target.write_text(f"VSVersionInfo(ffi=FixedFileInfo(filevers={numbers!r},prodvers={numbers!r},mask=0x3f,flags=2,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable('041604B0',[{items}])]),VarFileInfo([VarStruct('Translation',[1046,1200])])])",encoding='utf8')
def collect_notices(licenses):
    shutil.copytree(ROOT/'licenses',licenses)
    shutil.copy2(ROOT/'LICENSE',licenses/'PROJECT-GPL-3.0.txt')
    shutil.copy2(Path(sys.base_prefix)/'LICENSE.txt',licenses/'Python-LICENSE.txt')
    for dist in importlib.metadata.distributions():
        for f in dist.files or []:
            name=str(f).replace('\\','/')
            if '.dist-info/licenses/' in name or '.dist-info/sboms/' in name:
                src=Path(dist.locate_file(f));out=licenses/'python-packages'/name
                out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,out)
    shutil.copy2(ROOT/'docs/RESOURCE_RIGHTS.md',licenses/'RESOURCE_RIGHTS.md')
    shutil.copy2(ROOT/'docs/NATIVE_LICENSES.md',licenses/'NATIVE_LICENSES.md')
    (licenses/'CREDITS.txt').write_text('Império Aion — Jose Mardegam. Original code: GPL-3.0-only.\nPortions use the FreeType Project (https://freetype.org), copyright 1996-2026 its authors, under the FreeType License option.\nPython and its Windows runtime components retain their original notices, including Microsoft Distributable Code conditions in Python-LICENSE.txt.\nWinDivert uses the LGPL-3.0 option; complete source accompanies the library.\nGame artwork and data are not licensed under the project GPL; see RESOURCE_RIGHTS.md.\n',encoding='utf8')
def freeze(stage,source,entry,name,data,onefile,icon):
    folder=stage/name;folder.mkdir();temp=folder/'temp';temp.mkdir()
    version=folder/'version.txt';version_file(version,name+'.exe','Império Aion · '+('instalar e atualizar' if onefile else 'monitor de combate'))
    common=f"debug=False,strip=False,upx=False,console=False,icon={str(icon)!r},version={str(version)!r},uac_admin=False,uac_uiaccess=False"
    spec=f"a=Analysis([{str(entry)!r}],pathex=[{str(source)!r}],binaries=[],datas={data!r},hiddenimports=[],hookspath=[],hooksconfig={{}},runtime_hooks=[],excludes=[],noarchive=False,optimize=0)\npyz=PYZ(a.pure)\n"
    if onefile:spec+=f"exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name={name!r},{common})\n"
    else:spec+=f"exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name={name!r},{common})\ncoll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name={name!r})\n"
    specfile=folder/'build.spec';specfile.write_text(spec,encoding='utf8')
    bootstrap='import runpy,sys,tempfile;tempfile.tempdir=sys.argv.pop(1);sys.argv[0]="pyinstaller";runpy.run_module("PyInstaller",run_name="__main__")'
    with (folder/'build.log').open('w',encoding='utf8') as log:
        subprocess.run([sys.executable,'-X','utf8','-c',bootstrap,str(temp),'--noconfirm','--distpath',str(folder/'dist'),'--workpath',str(folder/'work'),str(specfile)],check=True,stdout=log,stderr=subprocess.STDOUT,
          env=dict(os.environ,PYINSTALLER_CONFIG_DIR=str(stage/'pyinstaller-cache'),PYTHONIOENCODING='utf8'),timeout=600)
    return folder/'dist'/(name+'.exe' if onefile else name)
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--vendor-cache',type=Path,default=ROOT/'work/vendor-cache')
    args=parser.parse_args();target=args.output.resolve()
    if os.name!='nt' or sys.version_info[:3]!=(3,14,7):raise SystemExit('Use CPython 3.14.7 x64 on Windows.')
    import struct
    if struct.calcsize('P')!=8:raise SystemExit('64-bit Python required.')
    for entry in json.loads((ROOT/'metadata/windows-wheels.json').read_text(encoding='utf8')):
        if importlib.metadata.version(entry['name'])!=entry['version']:
            raise SystemExit('Install the exact requirements-windows.lock before building.')
    subprocess.run([sys.executable,str(ROOT/'tools/audit_repository.py')],check=True)
    before=inputs();target.mkdir(parents=True,exist_ok=False)
    stage=ROOT/'build'/target.name;stage.mkdir(parents=True,exist_ok=False)
    app=stage/'app-source';updater=stage/'updater-source'
    for source,dest in [(ROOT/'src/imperio_aion',app),(ROOT/'src/updater',updater)]:
        shutil.copytree(source,dest,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    vendor(args.vendor_cache.resolve(),app/'vendor/windivert')
    sys.path.insert(0,str(updater));import updater_trust
    helper,helper_report=compile_helper(stage/'native',updater_trust.RSA_PUBLIC_XML,VERSION)
    shutil.copy2(helper,updater/'imperio-install.exe')
    # Pin this freshly built helper in STAGING only. Published runtime anchors stay unchanged.
    (updater/'helper_trust.py').write_text('HELPER_SHA256='+repr(sha(helper))+'\nHELPER_CONFIG_SHA256='+repr(sha(updater/'imperio-install.exe.config'))+'\n',encoding='utf8')
    collect_notices(updater/'licenses')
    updater_data=[(str(updater/n),'.') for n in ('imperio-install.exe','imperio-install.exe.config','imperioaion.ico','logo.png','legal.json','UNINSTALL.txt')]+[(str(updater/'licenses'),'licenses')]
    print('Building updater...',flush=True)
    launcher=freeze(stage,updater,updater/'updater_app.py','imperioaion-setup',updater_data,True,updater/'imperioaion.ico')
    shutil.copy2(launcher,target/'imperioaion-setup.exe')
    data=[(str(p),str(p.relative_to(app).parent)) for p in sorted(app.rglob('*')) if p.is_file() and p.suffix not in {'.py','.pyc'} and '__pycache__' not in p.parts]
    print('Building desktop application...',flush=True)
    runtime=freeze(stage,app,app/'desktop_entry.py','imperioaion',data,False,app/'assets/panel/imperioaion.ico')
    package=target/'application';shutil.copytree(runtime,package)
    shutil.copy2(launcher,package/'imperioaion-update.exe')
    licenses=package/'licenses';shutil.copytree(updater/'licenses',licenses)
    shutil.copy2(ROOT/'src/updater/UNINSTALL.txt',package/'UNINSTALL.txt')
    files=[dict(path=p.relative_to(package).as_posix(),size=p.stat().st_size,sha256=sha(p)) for p in sorted(package.rglob('*')) if p.is_file()]
    archive=target/('imperio-aion-'+VERSION+'-candidate.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for entry in files:z.write(package/entry['path'],'release/'+entry['path'])
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:raise RuntimeError('ZIP CRC failure')
    inventory={'product':'Imperio.AION','version':VERSION,'archive':{'name':archive.name,'size':archive.stat().st_size,'sha256':sha(archive),'prefix':'release/'},'files':files}
    write_json(target/'package-inventory.json',inventory)
    binaries=[f for f in files if Path(f['path']).suffix.lower() in {'.exe','.dll','.pyd','.sys'}]
    from PyInstaller.archive.readers import CArchiveReader
    embedded=[];reader=CArchiveReader(str(target/'imperioaion-setup.exe'))
    for name in sorted(reader.toc):
        if Path(name).suffix.lower() in {'.exe','.dll','.pyd','.sys'}:
            raw=reader.extract(name);embedded.append(dict(path=name,size=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
    write_json(target/'binary-inventory.json',{'files':binaries,'embedded_in_setup':embedded,'scope':'outer package and nested installer binaries; see NATIVE_LICENSES.md and upstream SBOMs for attribution'})
    write_json(target/'build-environment.json',{'python':sys.version,'packages':sorted([{'name':d.metadata['Name'],'version':d.version} for d in importlib.metadata.distributions()],key=lambda x:x['name'].lower())})
    if inputs()!=before:raise RuntimeError('Repository changed during build')
    revision=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    dirty=bool(subprocess.run(['git','status','--porcelain'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip())
    write_json(target/'build-proof.json',{'version':VERSION,'git_commit':revision,'git_dirty':dirty,'source_sha256':before,'helper':helper_report,'setup_sha256':sha(target/'imperioaion-setup.exe'),'archive_sha256':sha(archive),'authenticode':False,'production_manifest_signed':False,'installed':False,'capture_started':False,'private_keys_read':False})
    (target/'LEIA-PRIMEIRO.txt').write_text('IMPÉRIO AION — CANDIDATO DE BUILD\n\nAplicativo e instalador compilados do repositório separado. Ainda sem Authenticode.\nO setup começa sem consulta automática. Verificar consulta o canal configurado no site; NÃO instala automaticamente este ZIP candidato.\nPara publicar este pacote, é necessário autorizar seus hashes com as chaves de release e publicar os metadados correspondentes. As chaves atuais são do piloto.\nNão substitua o download público nem execute como administrador para contornar validações.\nDireitos de imagens e dados ainda pendentes. SignPath não aprovou o projeto.\n',encoding='utf8')
    print(json.dumps({'output':str(target),'files':len(files),'archive_sha256':sha(archive)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
