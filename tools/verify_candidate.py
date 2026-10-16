# SPDX-License-Identifier: GPL-3.0-only
"""Exercise real candidate without production install, network or game capture."""
import argparse,base64,hashlib,json,os,subprocess,sys,tempfile,zipfile
from pathlib import Path
from datetime import datetime,timedelta,timezone
from cryptography.hazmat.primitives.asymmetric import ed25519,rsa,padding
from cryptography.hazmat.primitives import hashes,serialization
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'src/updater')]
from build_native import compile_helper
from verify_core import verify_envelope,verify_zip

def main():
    parser=argparse.ArgumentParser();parser.add_argument('candidate',type=Path)
    parser.add_argument('--inventory-only',action='store_true',help='For elevated CI runners: verify bytes without launching GUIs or installer.')
    args=parser.parse_args();OUT=args.candidate.resolve()
    inventory=json.loads((OUT/'package-inventory.json').read_text(encoding='utf8'))
    archive=OUT/inventory['archive']['name']
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==inventory['archive']['sha256']
    with zipfile.ZipFile(archive) as z:
        assert set(z.namelist())=={'release/'+e['path'] for e in inventory['files']}
        for e in inventory['files']:
            data=z.read('release/'+e['path'])
            assert len(data)==e['size'] and hashlib.sha256(data).hexdigest()==e['sha256'],e['path']
    if args.inventory_only:
        report=dict(status='PASS',inventory_verified=True,files=len(inventory['files']),runtime_qa_executed=False,reason='CI inventory-only mode; separate unelevated runtime QA required')
        (OUT/'inventory-validation.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
        print(json.dumps(report));return
    (ROOT/'work').mkdir(exist_ok=True)
    qa=Path(tempfile.mkdtemp(prefix='candidate-qa-',dir=ROOT/'work'))
    temp=qa/'temp';temp.mkdir();data=qa/'user-data';data.mkdir()
    sentinel=data/'preserve.txt';sentinel.write_text('preserve user data',encoding='utf8')
    env=dict(os.environ,TEMP=str(temp),TMP=str(temp),LOCALAPPDATA=str(data),APPDATA=str(data))
    def run(exe,*args,success=True):
        p=subprocess.run([str(exe),*map(str,args)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180,creationflags=subprocess.CREATE_NO_WINDOW)
        if (p.returncode==0)!=success:raise RuntimeError(str(args)+' '+p.stdout.decode('utf8','replace')+' '+p.stderr.decode('utf8','replace'))
        return p.stdout.decode('utf8','replace')
    app_report=OUT/'app-check.json';ui_report=OUT/'installer-ui-check.json'
    run(OUT/'application/imperioaion.exe','--check','--output',app_report)
    app=json.loads(app_report.read_text(encoding='utf8'));assert app['status']=='PASS'
    run(OUT/'imperioaion-setup.exe','--check-ui','--destination',qa/'ui-state','--result',ui_report)
    ui=json.loads(ui_report.read_text(encoding='utf8'));assert ui['status']=='PASS'
    # One-use keys exist only in memory and are never exported or embedded in
    # delivered artifacts. The temporary verifier uses the same reviewed source.
    signing=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    n=signing.public_key().public_numbers()
    b64=lambda value:base64.b64encode(value).decode('ascii')
    xml='<RSAKeyValue><Modulus>'+b64(n.n.to_bytes(384,'big'))+'</Modulus><Exponent>AQAB</Exponent></RSAKeyValue>'
    helper,_=compile_helper(qa/'native-test-only',xml)
    inventory=json.loads((OUT/'package-inventory.json').read_text(encoding='utf8'))
    now=datetime.now(timezone.utc);stamp=lambda d:d.strftime('%Y-%m-%dT%H:%M:%SZ')
    payload=dict(inventory,schema=1,channel='pilot',release_counter=871,not_before=stamp(now-timedelta(minutes=5)),expires=stamp(now+timedelta(days=1)))
    raw=json.dumps(payload,separators=(',',':'),ensure_ascii=True).encode()
    manifest=qa/'candidate.install';manifest.write_text(json.dumps({'payload':b64(raw),'signature':b64(signing.sign(raw,padding.PKCS1v15(),hashes.SHA256()))}),encoding='utf8')
    archive=OUT/inventory['archive']['name']
    ed=ed25519.Ed25519PrivateKey.generate()
    envelope=json.dumps({'payload':b64(raw),'signature':b64(ed.sign(raw))}).encode()
    verified=verify_envelope(envelope,ed.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw),minimum_counter=857)
    verify_zip(archive,verified)
    run(helper,'--verify',archive,manifest)
    bad=qa/'wrong-signature.install';bad.write_text(json.dumps({'payload':b64(raw),'signature':b64(bytes(384))}),encoding='utf8')
    run(helper,'--verify',archive,bad,success=False)
    dest=qa/'isolated-install';dest.mkdir();(dest/'.imperio-local-test').write_text('',encoding='utf8')
    run(helper,'--test-install',archive,manifest,dest)
    installed=json.loads(run(helper,'--test-inspect-installed',dest))
    assert installed['status']=='installed' and installed['authenticated']
    installed_exe=dest/'versions'/inventory['version']/'imperioaion.exe'
    with installed_exe.open('ab') as stream:stream.write(b'TAMPER TEST')
    # Inspect uses a nonzero return code for unauthenticated inventories.
    bad_inspect=subprocess.run([str(helper),'--test-inspect-installed',str(dest)],env=env,capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
    bad_result=json.loads(bad_inspect.stdout.decode('utf8'))
    assert bad_result.get('authenticated') is not True
    run(helper,'--test-uninstall',dest)
    assert not dest.exists() and sentinel.read_text()=='preserve user data'
    report=dict(status='PASS',app_check='app-check.json',installer_ui='installer-ui-check.json',python_zip_verification=True,native_signature_verification=True,wrong_signature_rejected=True,tampered_install_rejected=True,isolated_install=True,isolated_uninstall=True,user_data_preserved=True,production_install_tested=False,registry_modified=False,capture_started=False,driver_started=False,private_release_keys_read=False,ephemeral_qa_keys_exported=False)
    (OUT/'validation.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8');print(json.dumps(report))
if __name__=='__main__':main()
