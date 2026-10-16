# SPDX-License-Identifier: GPL-3.0-only
"""Compile the native helper from public anchors; never run/install it."""
import hashlib,json,os,subprocess,sys,argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def compile_helper(target, xml, version='0.8.71'):
    source=ROOT/'src/updater'
    if not isinstance(xml,str) or not xml.startswith('<RSAKeyValue>') or any(t in xml for t in ('<D>','<P>','<Q>')):
        raise ValueError('Expected public RSA parameters only.')
    compiler=Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():raise SystemExit('Windows .NET Framework C# compiler required.')
    target=Path(target)
    target.mkdir(parents=True,exist_ok=False)
    generated=target/'Installer.generated.cs'
    import re
    if not re.fullmatch(r'0\.[0-9]{1,3}\.[0-9]{1,3}',version):raise ValueError('Invalid version')
    attributes='\n'.join('[assembly: System.Reflection.'+key+'("'+value+'")]' for key,value in {
      'AssemblyTitle':'Imperio Aion Installer','AssemblyProduct':'Império Aion',
      'AssemblyCompany':'Jose Mardegam','AssemblyVersion':version+'.0',
      'AssemblyFileVersion':version+'.0','AssemblyInformationalVersion':version}.items())
    text=(source/'Installer.cs').read_text(encoding='utf-8').replace('__RSA_PUBLIC_XML__',xml)
    generated.write_text(text.replace('class Installer {',attributes+'\nclass Installer {'),encoding='utf-8')
    executable=target/'imperio-install.exe'
    subprocess.run([str(compiler),'/nologo','/target:exe','/platform:x64','/optimize+',
      '/out:'+str(executable),'/reference:System.Web.Extensions.dll',
      '/reference:System.IO.Compression.dll','/reference:System.IO.Compression.FileSystem.dll',
      '/reference:System.Windows.Forms.dll',str(generated)],check=True)
    report={'sha256':hashlib.sha256(executable.read_bytes()).hexdigest(),'compiled':True,
            'executed':False,'installed':False,'updater_trust_updated':False,'authenticode':False}
    (target/'build-result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return executable,report

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'build/native')
    args=parser.parse_args()
    sys.path.insert(0,str(ROOT/'src/updater'))
    import updater_trust
    _,report=compile_helper(args.output,updater_trust.RSA_PUBLIC_XML)
    print(json.dumps(report))
if __name__=='__main__':main()
