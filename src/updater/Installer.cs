// SPDX-FileCopyrightText: 2026 Zepidem
// SPDX-License-Identifier: GPL-3.0-only
// Narrow privileged boundary. BCL/GAC only; no Python, PowerShell or network.
using System;
using System.IO;
using System.IO.Compression;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Runtime.InteropServices;
using System.Diagnostics;
using System.Threading;
using System.Xml;
using Microsoft.Win32;
using System.Windows.Forms;

class Installer {
    const string PublicKey = "__RSA_PUBLIC_XML__";
    const int MinimumCounter = 857;
    const long MaxZip = 209715200, MaxTotal = 314572800, MaxFile = 33554432;
    static JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength=2097152, RecursionLimit=12 };
    static string[] Writers = { "S-1-5-18", "S-1-5-32-544", "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464" };
    static bool Administrator() { return new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator); }
    const string MonitorName = @"Local\ImperioAionV1-monitor";
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr OpenMutex(uint access,bool inherit,string name);
    [DllImport("kernel32.dll",SetLastError=true)]static extern bool CloseHandle(IntPtr handle);
    static bool MonitorRunning(out string state) {
        IntPtr handle=OpenMutex(0x00100000,false,MonitorName);
        if(handle!=IntPtr.Zero){CloseHandle(handle);state="present";return true;}
        int error=Marshal.GetLastWin32Error();
        if(error==2){state="absent";return false;}
        // An inaccessible monitor must not be interpreted as a closed app.
        state="inaccessible";return true;
    }
    static Mutex ClaimClosedMonitor() {
        bool created=false;Mutex monitor=null;
        try {
            monitor=new Mutex(false,MonitorName,out created);
            Need(created,"Feche o Império AION pela HUD antes de instalar a atualização.");
            return monitor;
        }catch {if(monitor!=null)monitor.Dispose();throw;}
    }
    static void Need(bool value, string message) { if(!value) throw new InvalidDataException(message); }
    static string Hash(Stream stream) { using(var sha=SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant(); }
    static string HashFile(string path) { using(var f=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read)) return Hash(f); }
    static string Text(Dictionary<string,object> d,string name) { Need(d.ContainsKey(name)&&d[name] is string,"Campo de texto inválido: "+name); return (string)d[name]; }
    static long Number(Dictionary<string,object> d,string name) { Need(d.ContainsKey(name)&&(d[name] is int||d[name] is long),"Campo inteiro inválido: "+name); return Convert.ToInt64(d[name],CultureInfo.InvariantCulture); }
    static Dictionary<string,object> Obj(object item) { var d=item as Dictionary<string,object>; Need(d!=null,"Objeto JSON inválido."); return d; }
    static void White(string s,ref int i) {while(i<s.Length&&(s[i]==' '||s[i]=='\t'||s[i]=='\r'||s[i]=='\n'))i++;}
    static string JsonString(string s,ref int i) {
        Need(i<s.Length&&s[i]=='"',"Texto JSON inválido.");int first=i++;
        while(i<s.Length){char c=s[i++];if(c=='"')return s.Substring(first,i-first);if(c=='\\'){Need(i<s.Length,"Escape JSON incompleto.");i++;}}
        throw new InvalidDataException("Texto JSON incompleto.");
    }
    static void JsonValue(string s,ref int i,int depth) {
        Need(depth<=12,"JSON excede profundidade.");White(s,ref i);Need(i<s.Length,"JSON incompleto.");
        char begin=s[i];
        if(begin=='{') {
            i++;White(s,ref i);if(i<s.Length&&s[i]=='}'){i++;return;}
            var keys=new HashSet<string>(StringComparer.Ordinal);
            while(true){White(s,ref i);string name=(string)Json.DeserializeObject(JsonString(s,ref i));Need(keys.Add(name),"Propriedade JSON duplicada.");
                White(s,ref i);Need(i<s.Length&&s[i++]==':',"Objeto JSON inválido.");JsonValue(s,ref i,depth+1);White(s,ref i);
                Need(i<s.Length,"Objeto JSON incompleto.");char next=s[i++];if(next=='}')return;Need(next==',',"Objeto JSON inválido.");}
        }
        if(begin=='[') {
            i++;White(s,ref i);if(i<s.Length&&s[i]==']'){i++;return;}
            while(true){JsonValue(s,ref i,depth+1);White(s,ref i);Need(i<s.Length,"Lista JSON incompleta.");char next=s[i++];if(next==']')return;Need(next==',',"Lista JSON inválida.");}
        }
        if(begin=='"'){JsonString(s,ref i);return;}
        int start=i;while(i<s.Length&&s[i]!=','&&s[i]!='}'&&s[i]!=']'&&s[i]!=' '&&s[i]!='\t'&&s[i]!='\r'&&s[i]!='\n')i++;
        Need(i>start,"Valor JSON inválido.");
    }
    static Dictionary<string,object> StrictObj(byte[] raw) {
        string text=new UTF8Encoding(false,true).GetString(raw);int i=0;JsonValue(text,ref i,0);White(text,ref i);Need(i==text.Length,"Conteúdo após JSON.");
        return Obj(Json.DeserializeObject(text));
    }
    static void Keys(Dictionary<string,object>d,params string[] fields) { Need(d.Count==fields.Length,"Campos desconhecidos no manifesto."); foreach(string f in fields)Need(d.ContainsKey(f),"Campo ausente: "+f); }
    static void Plain(string path) { var a=File.GetAttributes(path); Need((a&FileAttributes.ReparsePoint)==0,"Link/junction não permitido: "+path); }
    static string Portable(string path) {
        Need(path.Length>0&&path.Length<=240&&!path.StartsWith("/")&&!path.EndsWith("/"),"Caminho inválido.");
        foreach(string part in path.Split('/')) {
            Need(part.Length>0&&part!="."&&part!=".."&&part.TrimEnd(' ','.')==part,"Caminho ambíguo.");
            Need(Regex.IsMatch(part,@"^[A-Za-z0-9_ .()@+\-]+$"),"Caminho não portátil.");
            string stem=part.Split('.')[0].TrimEnd(' ');
            Need(!Regex.IsMatch(stem,@"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$",RegexOptions.IgnoreCase),"Nome reservado.");
        } return path;
    }
    static string Child(string root,string relative) {
        Portable(relative); string p=Path.GetFullPath(Path.Combine(root,relative.Replace('/',Path.DirectorySeparatorChar)));
        Need(p.StartsWith(Path.GetFullPath(root)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase),"Caminho fora da instalação."); return p;
    }
    // Schema 1 is the legacy direct RSA envelope. Schema 2 has an immutable
    // root authorization plus a separately scoped freshness envelope. Both are
    // rechecked here; unprivileged Python verification is never an authority.
    static Dictionary<string,object> ReleaseEnvelope(byte[] envelope,out Dictionary<string,object> freshness) {
        Need(envelope.Length<=2097152,"Manifesto excede o limite.");
        var outer=StrictObj(envelope);
        freshness=null;
        if(outer.ContainsKey("release")||outer.ContainsKey("freshness")) {
            Keys(outer,"release","freshness");freshness=Obj(outer["freshness"]);outer=Obj(outer["release"]);
        }
        Keys(outer,"payload","signature");return outer;
    }
    static byte[] ReleasePayload(byte[] envelope) {
        Dictionary<string,object> unused;return Convert.FromBase64String(Text(ReleaseEnvelope(envelope,out unused),"payload"));
    }
    static string HashBytes(byte[] bytes) {using(var stream=new MemoryStream(bytes,false))return Hash(stream);}
    static long RenewalCounter(byte[] envelope) {
        Dictionary<string,object> freshness;ReleaseEnvelope(envelope,out freshness);
        if(freshness==null)return 0;
        var m=StrictObj(Convert.FromBase64String(Text(freshness,"payload")));
        return Number(m,"renewal_counter");
    }
    static string FreshnessDigest(byte[] envelope) {
        Dictionary<string,object> freshness;ReleaseEnvelope(envelope,out freshness);
        return freshness==null?null:HashBytes(Convert.FromBase64String(Text(freshness,"payload")));
    }
    static DateTime Stamp(Dictionary<string,object> value,string field) {
        string text=Text(value,field);Need(Regex.IsMatch(text,@"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"),"Data UTC inválida.");
        return DateTime.ParseExact(text,"yyyy-MM-dd'T'HH:mm:ss'Z'",CultureInfo.InvariantCulture,DateTimeStyles.AssumeUniversal|DateTimeStyles.AdjustToUniversal);
    }
    static void RenewalRsa(RSACryptoServiceProvider rsa,string xml) {
        Need(xml.Length>0&&xml.Length<=4096&&!xml.Contains("<!"),"Chave RSA de renovação inválida.");
        var document=new XmlDocument();document.XmlResolver=null;
        var settings=new XmlReaderSettings{DtdProcessing=DtdProcessing.Prohibit,XmlResolver=null};
        using(var text=new StringReader(xml))using(var reader=XmlReader.Create(text,settings))document.Load(reader);
        var root=document.DocumentElement;Need(root!=null&&root.Name=="RSAKeyValue"&&root.Attributes.Count==0,"Chave RSA de renovação inválida.");
        var children=new List<XmlElement>();
        foreach(XmlNode child in root.ChildNodes){var element=child as XmlElement;if(element!=null)children.Add(element);else Need(String.IsNullOrWhiteSpace(child.InnerText),"Chave RSA de renovação inválida.");}
        Need(children.Count==2&&children[0].Name=="Modulus"&&children[1].Name=="Exponent","Somente chave RSA pública é permitida.");
        foreach(var child in children){Need(child.Attributes.Count==0&&child.ChildNodes.Count==1&&child.FirstChild.NodeType==XmlNodeType.Text,"Chave RSA de renovação inválida.");}
        byte[] modulus=Convert.FromBase64String(children[0].InnerText),exponent=Convert.FromBase64String(children[1].InnerText);
        Need(modulus.Length==384&&(modulus[0]&0x80)!=0&&Convert.ToBase64String(exponent)=="AQAB","Use RSA-3072 com expoente 65537.");
        rsa.FromXmlString(xml);Need(rsa.PublicOnly&&rsa.KeySize==3072,"Somente chave RSA pública de 3072 bits é permitida.");
        using(var publisher=new RSACryptoServiceProvider()){
            publisher.PersistKeyInCsp=false;publisher.FromXmlString(PublicKey);
            Need(Convert.ToBase64String(rsa.ExportParameters(false).Modulus)!=Convert.ToBase64String(publisher.ExportParameters(false).Modulus),"A chave de renovação deve ser diferente da raiz offline.");
        }
    }
    static void Freshness(Dictionary<string,object> envelope,byte[] releaseRaw,Dictionary<string,object> release,bool dates) {
        Need(envelope!=null,"Referência de validade ausente.");Keys(envelope,"payload","ed25519_signature","rsa_signature");
        Need(Encoding.UTF8.GetByteCount(Json.Serialize(envelope))<=16384,"Envelope de validade excede limite.");
        byte[] raw=Convert.FromBase64String(Text(envelope,"payload")),signature=Convert.FromBase64String(Text(envelope,"rsa_signature"));
        byte[] ed=Convert.FromBase64String(Text(envelope,"ed25519_signature"));
        Need(raw.Length>0&&raw.Length<=4096&&signature.Length==384&&ed.Length==64,"Assinatura de validade inválida.");
        var renewal=Obj(release["renewal"]);Keys(renewal,"ed25519_public_key","rsa_public_xml","max_lifetime_seconds");
        Need(Regex.IsMatch(Text(renewal,"ed25519_public_key"),"^[a-f0-9]{64}$")&&Text(renewal,"rsa_public_xml").Length<=4096,"Delegação inválida.");
        long lifetime=Number(renewal,"max_lifetime_seconds");Need(lifetime>=1&&lifetime<=2592000,"Validade delegada excede 30 dias.");
        using(var rsa=new RSACryptoServiceProvider()) {
            rsa.PersistKeyInCsp=false;RenewalRsa(rsa,Text(renewal,"rsa_public_xml"));
            Need(rsa.KeySize==3072&&rsa.VerifyData(raw,CryptoConfig.MapNameToOID("SHA256"),signature),"Assinatura de validade recusada.");
        }
        var m=StrictObj(raw);
        Keys(m,"schema","product","channel","release_counter","release_payload_sha256","renewal_counter","not_before","expires");
        Need(Number(m,"schema")==1&&Text(m,"product")==Text(release,"product")&&Text(m,"channel")==Text(release,"channel")&&
            Number(m,"release_counter")==Number(release,"release_counter")&&Number(m,"renewal_counter")>=1&&
            Text(m,"release_payload_sha256")==HashBytes(releaseRaw),"Validade não corresponde à versão autorizada.");
        DateTime begin=Stamp(m,"not_before"),end=Stamp(m,"expires");
        Need(begin<end&&(end-begin).TotalSeconds<=lifetime&&begin>=Stamp(release,"not_before")&&end<=Stamp(release,"expires"),"Prazo de validade fora da delegação.");
        Need(!dates||(begin<=DateTime.UtcNow&&DateTime.UtcNow<end),"Referência de validade expirada ou ainda não válida.");
    }
    static Dictionary<string,object> Manifest(byte[] envelope,bool dates) {
        Dictionary<string,object> freshness;var outer=ReleaseEnvelope(envelope,out freshness);
        byte[] raw=Convert.FromBase64String(Text(outer,"payload")), signature=Convert.FromBase64String(Text(outer,"signature"));
        Need(raw.Length<=1048576&&signature.Length==384,"Assinatura inválida.");
        using(var rsa=new RSACryptoServiceProvider()) { rsa.PersistKeyInCsp=false; rsa.FromXmlString(PublicKey); Need(rsa.VerifyData(raw,CryptoConfig.MapNameToOID("SHA256"),signature),"Assinatura do instalador recusada."); }
        var m=StrictObj(raw);
        long schema=Number(m,"schema");
        if(schema==2)Keys(m,"schema","product","channel","version","release_counter","not_before","expires","archive","files","renewal");
        else {Keys(m,"schema","product","channel","version","release_counter","not_before","expires","archive","files");Need(schema==1&&freshness==null,"Schema/validade incompatível.");}
        Need(Text(m,"product")=="Imperio.AION"&&Text(m,"channel")=="pilot","Produto/canal inválido.");
        Need(Regex.IsMatch(Text(m,"version"),@"^0\.[0-9]{1,3}\.[0-9]{1,3}$")&&Number(m,"release_counter")>=MinimumCounter,"Versão inválida ou antiga.");
        DateTime begin=Stamp(m,"not_before"),end=Stamp(m,"expires");
        Need(begin<end&&(!dates||(begin<=DateTime.UtcNow&&DateTime.UtcNow<end)),"Referência expirada ou ainda não válida.");
        if(schema==2){Need((end-begin).TotalSeconds<=31536000,"Autorização raiz excede 365 dias.");Freshness(freshness,raw,m,dates);}
        var archive=Obj(m["archive"]);Keys(archive,"name","size","sha256","prefix");
        Portable(Text(archive,"name"));Need(!Text(archive,"name").Contains("/"),"Nome ZIP inválido.");
        Need(Number(archive,"size")>0&&Number(archive,"size")<=MaxZip&&Regex.IsMatch(Text(archive,"sha256"),"^[a-f0-9]{64}$"),"ZIP inválido.");
        string prefix=Text(archive,"prefix");Need(prefix.EndsWith("/"),"Raiz ZIP inválida.");Portable(prefix.TrimEnd('/'));
        Records(m); return m;
    }
    static Dictionary<string,Dictionary<string,object>> Records(Dictionary<string,object> m) {
        var entries=m["files"] as object[];Need(entries!=null&&entries.Length>0&&entries.Length<=2000,"Inventário inválido.");
        var records=new Dictionary<string,Dictionary<string,object>>(StringComparer.OrdinalIgnoreCase);long size=0;
        foreach(var item in entries) {var f=Obj(item);Keys(f,"path","size","sha256");string p=Portable(Text(f,"path"));long n=Number(f,"size");
            Need(n>=0&&n<=MaxFile&&Regex.IsMatch(Text(f,"sha256"),"^[a-f0-9]{64}$")&&!records.ContainsKey(p),"Registro inválido/duplicado.");records.Add(p,f);size+=n;
        } Need(size<=MaxTotal&&records.ContainsKey("imperioaion.exe"),"Inventário incompleto/excessivo.");
        foreach(string p in records.Keys) {string parent=p;while(parent.Contains("/")){parent=parent.Substring(0,parent.LastIndexOf('/'));Need(!records.ContainsKey(parent),"Alias arquivo/pasta.");}}return records;
    }
    static void Acl(string path) {
        Plain(path);var directory=Directory.Exists(path);
        FileSystemSecurity security=directory?(FileSystemSecurity)Directory.GetAccessControl(path):(FileSystemSecurity)File.GetAccessControl(path);
        var owner=(SecurityIdentifier)security.GetOwner(typeof(SecurityIdentifier));Need(Array.IndexOf(Writers,owner.Value)>=0,"Proprietário não protegido: "+path);
        var descriptor=new RawSecurityDescriptor(security.GetSecurityDescriptorBinaryForm(),0);Need(descriptor.DiscretionaryAcl!=null,"DACL nula.");
        foreach(GenericAce ace in descriptor.DiscretionaryAcl)Need(ace is CommonAce&&(ace.AceType==AceType.AccessAllowed||ace.AceType==AceType.AccessDenied),"ACE avançada não validada.");
        const FileSystemRights write = FileSystemRights.Write|FileSystemRights.Delete|FileSystemRights.DeleteSubdirectoriesAndFiles|FileSystemRights.ChangePermissions|FileSystemRights.TakeOwnership;
        var rules=security.GetAccessRules(true,true,typeof(SecurityIdentifier));Need(rules.Count>0,"DACL vazia/nula.");
        foreach(FileSystemAccessRule rule in rules) if(rule.AccessControlType==AccessControlType.Allow&&(rule.PropagationFlags&PropagationFlags.InheritOnly)==0&&
            (((uint)rule.FileSystemRights&(uint)write)!=0||((uint)rule.FileSystemRights&0x50000000)!=0))
            Need(Array.IndexOf(Writers,((SecurityIdentifier)rule.IdentityReference).Value)>=0,"Permissão de escrita não protegida: "+path);
    }
    static void TreeAcl(string path) { Acl(path);foreach(string item in Directory.GetFileSystemEntries(path)){Plain(item);if(Directory.Exists(item))TreeAcl(item);else Acl(item);} }
    static DirectorySecurity ProtectedAcl() {
        var s=new DirectorySecurity();s.SetAccessRuleProtection(true,false);s.SetOwner(new SecurityIdentifier("S-1-5-32-544"));
        foreach(string sid in new[]{"S-1-5-18","S-1-5-32-544"})s.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid),FileSystemRights.FullControl,InheritanceFlags.ContainerInherit|InheritanceFlags.ObjectInherit,PropagationFlags.None,AccessControlType.Allow));
        s.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier("S-1-5-32-545"),FileSystemRights.ReadAndExecute,InheritanceFlags.ContainerInherit|InheritanceFlags.ObjectInherit,PropagationFlags.None,AccessControlType.Allow));return s;
    }
    static void Seal(string path) {
        Plain(path);
        if(Directory.Exists(path))Directory.SetAccessControl(path,ProtectedAcl());
        else {
            var s=new FileSecurity();s.SetAccessRuleProtection(true,false);s.SetOwner(new SecurityIdentifier("S-1-5-32-544"));
            foreach(string sid in new[]{"S-1-5-18","S-1-5-32-544"})s.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid),FileSystemRights.FullControl,AccessControlType.Allow));
            s.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier("S-1-5-32-545"),FileSystemRights.ReadAndExecute,AccessControlType.Allow));File.SetAccessControl(path,s);
        }
    }
    static void SealTree(string path) {Seal(path);if(Directory.Exists(path))foreach(string p in Directory.GetFileSystemEntries(path))SealTree(p);}
    [DllImport("shell32.dll")]static extern int SHGetKnownFolderPath([MarshalAs(UnmanagedType.LPStruct)] Guid id,uint flags,IntPtr token,out IntPtr path);
    static string ProgramFiles() { IntPtr p;int result=SHGetKnownFolderPath(new Guid("6D809377-6AF0-444B-8957-A3773F02200E"),0,IntPtr.Zero,out p);Need(result==0,"Program Files indisponível.");try{return Marshal.PtrToStringUni(p);}finally{Marshal.FreeCoTaskMem(p);} }
    static byte[] ReadSmall(string path) { PlainAncestors(path);using(var f=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read)){Need(f.Length<=2097152,"Arquivo excede limite.");var b=new byte[(int)f.Length];int offset=0,n;while(offset<b.Length&&(n=f.Read(b,offset,b.Length-offset))>0)offset+=n;Need(offset==b.Length,"Leitura incompleta.");return b;} }
    static void PlainAncestors(string path) {string p=Path.GetFullPath(path);while(!String.IsNullOrEmpty(p)){Plain(p);var parent=Directory.GetParent(p);if(parent==null)break;p=parent.FullName;} }
    static void Inventory(string root,Dictionary<string,object> manifest) {
        Plain(root);var expected=Records(manifest);var found=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var dirs=new HashSet<string>(StringComparer.OrdinalIgnoreCase);foreach(string name in expected.Keys){string p=name;while(p.Contains("/")){p=p.Substring(0,p.LastIndexOf('/'));dirs.Add(p);}}
        var pending=new Stack<string>();pending.Push(root);
        while(pending.Count>0){string d=pending.Pop();foreach(string p in Directory.GetFileSystemEntries(d)){Plain(p);string relative=p.Substring(root.Length+1).Replace('\\','/');
            if(Directory.Exists(p)){Need(dirs.Contains(relative),"Diretório extra não autenticado: "+relative);pending.Push(p);}
            else {Need(expected.ContainsKey(relative),"Arquivo extra não autenticado: "+relative);var item=expected[relative];Need(new FileInfo(p).Length==Number(item,"size")&&HashFile(p)==Text(item,"sha256"),"Arquivo alterado: "+relative);found.Add(relative);}
        }} Need(found.Count==expected.Count,"Arquivos autenticados ausentes.");
    }
    static void Extract(FileStream input,string target,Dictionary<string,object> m) {
        var a=Obj(m["archive"]);Need(input.Length==Number(a,"size"),"Tamanho ZIP divergente.");Need(Hash(input)==Text(a,"sha256"),"SHA256 ZIP divergente.");input.Position=0;
        Preflight(input);input.Position=0;
        var expected=Records(m);var seen=new HashSet<string>(StringComparer.OrdinalIgnoreCase);long total=0;
        using(var zip=new ZipArchive(input,ZipArchiveMode.Read,true)) {
            Need(zip.Entries.Count<=4000,"ZIP contém entradas em excesso.");
            foreach(var e in zip.Entries) {
                Need(!e.FullName.EndsWith("/"),"Use ZIP sem entradas de diretórios explícitas.");
                Need(e.FullName.StartsWith(Text(a,"prefix"),StringComparison.Ordinal),"ZIP fora da raiz.");
                string rel=Portable(e.FullName.Substring(Text(a,"prefix").Length));Need(seen.Add(rel)&&expected.ContainsKey(rel),"ZIP extra/duplicado.");
                int mode=(e.ExternalAttributes>>16)&0xF000;Need((mode==0||mode==0x8000)&&(e.ExternalAttributes&0x400)==0,"ZIP especial/link.");
                var f=expected[rel];Need(e.Length==Number(f,"size")&&e.Length<=Math.Max(1,e.CompressedLength)*100,"Expansão ZIP inválida.");
                string dest=Child(target,rel);Directory.CreateDirectory(Path.GetDirectoryName(dest));
                using(var source=e.Open())using(var output=new FileStream(dest,FileMode.CreateNew,FileAccess.Write,FileShare.None)) {
                    byte[] buffer=new byte[65536];long count=0;int n;
                    while((n=source.Read(buffer,0,buffer.Length))>0){count+=n;total+=n;Need(count<=e.Length&&total<=MaxTotal,"Expansão ZIP excedeu limite.");output.Write(buffer,0,n);}Need(count==e.Length,"ZIP truncado.");output.Flush(true);
                }Need(HashFile(dest)==Text(f,"sha256"),"Conteúdo extraído divergente.");
            } Need(seen.Count==expected.Count,"ZIP incompleto.");
        } Inventory(target,m);
    }
    static void Preflight(FileStream input) {
        int length=(int)Math.Min(input.Length,65557);byte[] tail=new byte[length];input.Position=input.Length-length;
        int got=0,n;while(got<length&&(n=input.Read(tail,got,length-got))>0)got+=n;Need(got==length,"Índice ZIP truncado.");
        int e=-1;for(int i=length-22;i>=0;i--)if(BitConverter.ToUInt32(tail,i)==0x06054b50&&i+22+BitConverter.ToUInt16(tail,i+20)==length){e=i;break;}
        Need(e>=0,"Índice ZIP ausente.");int count=BitConverter.ToUInt16(tail,e+10);
        uint size=BitConverter.ToUInt32(tail,e+12),offset=BitConverter.ToUInt32(tail,e+16);
        Need(BitConverter.ToUInt16(tail,e+4)==0&&BitConverter.ToUInt16(tail,e+6)==0&&BitConverter.ToUInt16(tail,e+8)==count&&count>0&&count<=2000,"ZIP multidisco/ZIP64/excessivo.");
        Need(size<=2097152&&offset+((long)size)==input.Length-length+e,"Índice ZIP fora do limite.");
    }
    static long InstalledCounter(string root,Dictionary<string,object> candidate,byte[] candidateEnvelope) {
        string dir=Path.Combine(root,"receipts");if(!Directory.Exists(dir))return MinimumCounter;long max=MinimumCounter;
        foreach(string path in Directory.GetFiles(dir,"*.install")){var saved=ReadSmall(path);var m=Manifest(saved,false);
            if(Number(m,"release_counter")==Number(candidate,"release_counter")) {
                Need(HashBytes(ReleasePayload(saved))==HashBytes(ReleasePayload(candidateEnvelope)),"Contador reutilizado com outro conteúdo.");
                long accepted=RenewalCounter(saved),incoming=RenewalCounter(candidateEnvelope);
                Need(incoming>=accepted,"Regressão de validade recusada.");
                if(incoming==accepted)Need(FreshnessDigest(saved)==FreshnessDigest(candidateEnvelope),"Contador de validade reutilizado com outro conteúdo.");
            }
            max=Math.Max(max,Number(m,"release_counter"));}return max;
    }
    static void Atomic(string path,byte[] bytes) {
        string temp=path+"."+Guid.NewGuid().ToString("N")+".tmp";
        using(var f=new FileStream(temp,FileMode.CreateNew,FileAccess.Write,FileShare.None)){f.Write(bytes,0,bytes.Length);f.Flush(true);}
        if(File.Exists(path))File.Replace(temp,path,null);else File.Move(temp,path);
    }
    static void Install(string zip,string metadata,string root,bool test) {
        if(test){InstallCore(zip,metadata,root,true);return;}
        // Hold the named-object handle during the protected transaction. A new
        // app cannot become the primary instance while its installation changes.
        using(var monitor=ClaimClosedMonitor())InstallCore(zip,metadata,root,false);
    }
    static void InstallCore(string zip,string metadata,string root,bool test) {
        Need(!test||!Administrator(),"O teste isolado não deve ser executado como administrador.");
        var envelope=ReadSmall(metadata);var m=Manifest(envelope,true);
        if(!test){Need(Administrator(),"A instalação protegida exige administrador.");string pf=ProgramFiles();Acl(pf);
            root=Path.Combine(pf,"ImperioAION");if(Directory.Exists(root))TreeAcl(root);else Directory.CreateDirectory(root,ProtectedAcl());TreeAcl(root);
        }else {root=Path.GetFullPath(root);PlainAncestors(root);Need(File.Exists(Path.Combine(root,".imperio-local-test")),"Pasta de teste sem marcador explícito.");}
        string version=Text(m,"version"),versions=Path.Combine(root,"versions"),receipts=Path.Combine(root,"receipts"),staging=Path.Combine(root,"staging");
        Directory.CreateDirectory(versions);Directory.CreateDirectory(receipts);Directory.CreateDirectory(staging);
        if(!test){Seal(versions);Seal(receipts);Seal(staging);}
        using(var installLock=new FileStream(Path.Combine(root,"install.lock"),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)) {
            if(!test)Seal(Path.Combine(root,"install.lock"));
            Need(Number(m,"release_counter")>=InstalledCounter(root,m,envelope),"Regressão de versão recusada.");
            string final=Path.Combine(versions,version),receipt=Path.Combine(receipts,version+".install");
            if(Directory.Exists(final)){
                Inventory(final,m);Need(File.Exists(receipt),"Versão existente sem referência.");var priorEnvelope=ReadSmall(receipt);Manifest(priorEnvelope,false);
                Need(HashBytes(ReleasePayload(priorEnvelope))==HashBytes(ReleasePayload(envelope)),"Versão existente não corresponde à referência.");
                // Only freshness may change. Preserve the immutable installed files
                // and persist the accepted monotonic counter in the protected receipt.
                if(RenewalCounter(envelope)>RenewalCounter(priorEnvelope)){Atomic(receipt,envelope);if(!test)Seal(receipt);}
            }
            else {
                string stage=Path.Combine(staging,Guid.NewGuid().ToString("N"));Directory.CreateDirectory(stage);PlainAncestors(zip);
                // Deny write/delete sharing for the ENTIRE hash/extraction interval.
                using(var input=new FileStream(zip,FileMode.Open,FileAccess.Read,FileShare.Read))Extract(input,stage,m);
                if(!test){SealTree(stage);TreeAcl(stage);}
                Atomic(receipt,envelope);if(!test)Seal(receipt);Directory.Move(stage,final);
            }
            string previous=null,current=Path.Combine(root,"installed.json");if(File.Exists(current)){
                var prior=Obj(Json.DeserializeObject(Encoding.UTF8.GetString(ReadSmall(current))));string active=Text(prior,"version");
                previous=active==version?(prior.ContainsKey("previous")?prior["previous"] as string:null):active;
            }
            var state=new Dictionary<string,object>{{"schema",1},{"version",version},{"release_counter",Number(m,"release_counter")},{"previous",previous},{"accepted_at",DateTime.UtcNow.ToString("o")}};
            if(!test)RegisterProduct(root,version);
            Atomic(current,Encoding.UTF8.GetBytes(Json.Serialize(state)));if(!test){Seal(current);TreeAcl(root);}
            Console.WriteLine("PASS · instalação autenticada · "+version+" · "+Records(m).Count+" arquivos · "+root);
        }
    }
    static void Rollback(string root,bool test) {
        if(test){RollbackCore(root,true);return;}
        using(var monitor=ClaimClosedMonitor())RollbackCore(root,false);
    }
    static void RollbackCore(string root,bool test) {
        Need(!test||!Administrator(),"Teste isolado exige usuário comum.");
        if(!test){Need(Administrator(),"Recuperação protegida exige administrador.");root=Path.Combine(ProgramFiles(),"ImperioAION");TreeAcl(root);}
        else{root=Path.GetFullPath(root);PlainAncestors(root);Need(File.Exists(Path.Combine(root,".imperio-local-test")),"Marcador de teste ausente.");}
        using(var installLock=new FileStream(Path.Combine(root,"install.lock"),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None)){
            string path=Path.Combine(root,"installed.json");var state=Obj(Json.DeserializeObject(Encoding.UTF8.GetString(ReadSmall(path))));
            string active=Text(state,"version"),version=Text(state,"previous");Need(Regex.IsMatch(version,@"^0\.[0-9]{1,3}\.[0-9]{1,3}$"),"Sem versão anterior recuperável.");
            var manifest=Manifest(ReadSmall(Path.Combine(root,"receipts",version+".install")),false);
            Need(Text(manifest,"version")==version,"Recibo de recuperação divergente.");Inventory(Path.Combine(root,"versions",version),manifest);
            state["version"]=version;state["release_counter"]=Number(manifest,"release_counter");state["previous"]=active;
            Atomic(path,Encoding.UTF8.GetBytes(Json.Serialize(state)));if(!test)Seal(path);Console.WriteLine("PASS · recuperação autenticada · "+version);
        }
    }
    static void Launch(bool execute,bool background=false,bool capture=false) {
        string root=Path.Combine(ProgramFiles(),"ImperioAION");TreeAcl(root);
        var state=Obj(Json.DeserializeObject(Encoding.UTF8.GetString(ReadSmall(Path.Combine(root,"installed.json")))));
        string version=Text(state,"version");Need(Regex.IsMatch(version,@"^0\.[0-9]{1,3}\.[0-9]{1,3}$"),"Estado inválido.");
        var m=Manifest(ReadSmall(Path.Combine(root,"receipts",version+".install")),false);
        Need(Text(m,"version")==version&&Number(m,"release_counter")==Number(state,"release_counter"),"Estado e assinatura divergem.");
        string installed=Path.Combine(root,"versions",version);Inventory(installed,m);
        if(execute){
            Need(!Administrator(),"Abra o atualizador normalmente para manter a identidade da sua conta.");
            Need(Number(m,"release_counter")>=858,"Atualize para 0.8.58 para continuar com este atualizador.");
            string runningState;
            if(capture)for(int n=0;n<50&&MonitorRunning(out runningState);n++)Thread.Sleep(100);
            if(background&&MonitorRunning(out runningState))return;
            Need(!MonitorRunning(out runningState),"O Império AION já está aberto. Feche-o pela HUD antes de abrir outra versão.");
            string sid=WindowsIdentity.GetCurrent().User.Value;
            var process=new ProcessStartInfo(Path.Combine(installed,"imperioaion.exe"));
            process.Arguments="--expected-user-sid "+sid+(capture?" --auto-capture":"");
            process.UseShellExecute=!background;if(!background)process.Verb="runas";
            process.CreateNoWindow=background;process.WorkingDirectory=installed;Process.Start(process);
        }
        Console.WriteLine("PASS · versão instalada autenticada · "+version);
    }
    static void Inspect(string root,bool test) {
        string runningState="test-isolated";bool running=test?false:MonitorRunning(out runningState);
        var result=new Dictionary<string,object>{{"status","missing"},{"version",null},{"counter",null},
            {"running",running},{"running_state",runningState},{"authenticated",false},{"launch_supported",false},{"reason",null}};
        try {
            if(!test)root=Path.Combine(ProgramFiles(),"ImperioAION");else root=Path.GetFullPath(root);
            if(!Directory.Exists(root)&&!File.Exists(root)){Console.WriteLine(Json.Serialize(result));return;}
            if(test){PlainAncestors(root);Need(File.Exists(Path.Combine(root,".imperio-local-test")),"Pasta de teste sem marcador explícito.");}
            else TreeAcl(root);
            var state=Obj(Json.DeserializeObject(Encoding.UTF8.GetString(ReadSmall(Path.Combine(root,"installed.json")))));
            Keys(state,"schema","version","release_counter","previous","accepted_at");Need(Number(state,"schema")==1,"Estado instalado inválido.");
            string version=Text(state,"version");Need(Regex.IsMatch(version,@"^0\.[0-9]{1,3}\.[0-9]{1,3}$"),"Versão instalada inválida.");
            var manifest=Manifest(ReadSmall(Path.Combine(root,"receipts",version+".install")),false);
            Need(Text(manifest,"version")==version&&Number(manifest,"release_counter")==Number(state,"release_counter"),"Estado e recibo assinado divergem.");
            Inventory(Path.Combine(root,"versions",version),manifest);
            result["status"]="installed";result["version"]=version;result["counter"]=Number(manifest,"release_counter");
            result["authenticated"]=true;result["launch_supported"]=Number(manifest,"release_counter")>=858;
        }catch(Exception error){result["status"]="corrupted";result["reason"]=error.Message;}
        Console.WriteLine(Json.Serialize(result));
    }
    static void LauncherInfo(string root,bool test) {
        if(!test){root=Path.Combine(ProgramFiles(),"ImperioAION");TreeAcl(root);}
        else {root=Path.GetFullPath(root);PlainAncestors(root);Need(File.Exists(Path.Combine(root,".imperio-local-test")),"Pasta de teste sem marcador explícito.");}
        var state=StrictObj(ReadSmall(Path.Combine(root,"installed.json")));
        Keys(state,"schema","version","release_counter","previous","accepted_at");Need(Number(state,"schema")==1,"Estado instalado inválido.");
        string version=Text(state,"version");Need(Regex.IsMatch(version,@"^0\.[0-9]{1,3}\.[0-9]{1,3}$"),"Versão instalada inválida.");
        var manifest=Manifest(ReadSmall(Path.Combine(root,"receipts",version+".install")),false);
        Need(Text(manifest,"version")==version&&Number(manifest,"release_counter")==Number(state,"release_counter"),"Estado e recibo assinado divergem.");
        string installed=Path.Combine(root,"versions",version);Inventory(installed,manifest);
        Need(Records(manifest).ContainsKey("imperioaion-update.exe"),"Esta versão ainda não contém o iniciador autenticado.");
        Console.WriteLine(Json.Serialize(new Dictionary<string,object>{{"status","VERIFIED_LAUNCHER"},{"version",version},{"path",Child(installed,"imperioaion-update.exe")}}));
    }
    const string UninstallKey=@"Software\Microsoft\Windows\CurrentVersion\Uninstall\ImperioAION";
    static string Quote(string value){Need(!value.Contains("\""),"Caminho inválido.");return "\""+value+"\"";}
    static Dictionary<string,object> Registration(string root,string version) {
        return new Dictionary<string,object>{{"DisplayName","Império AION"},{"DisplayVersion",version},{"Publisher","Zepidem"},
            {"InstallLocation",root},{"DisplayIcon",Path.Combine(root,"versions",version,"imperioaion.exe")+",0"},
            {"UninstallString",Quote(Path.Combine(root,"maintenance","imperio-install.exe"))+" --uninstall"},
            {"URLInfoAbout","https://imperiobdo.com/aion2"},{"HelpLink","mailto:support@imperiobdo.com"},{"NoModify",1},{"NoRepair",1}};
    }
    static void RegisterProduct(string root,string version) {
        // Called only after signed inventory and protected install commit.
        string own=Process.GetCurrentProcess().MainModule.FileName;
        string folder=Path.Combine(root,"maintenance");Directory.CreateDirectory(folder);Seal(folder);
        foreach(string suffix in new[]{"",".config"}){
            string source=own+suffix,dest=Path.Combine(folder,"imperio-install.exe"+suffix);
            PlainAncestors(source);if(File.Exists(dest))Acl(dest);
            Atomic(dest,File.ReadAllBytes(source));Seal(dest);
        }
        using(var key=Registry.LocalMachine.CreateSubKey(UninstallKey))
            foreach(var pair in Registration(root,version))key.SetValue(pair.Key,pair.Value);
    }
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool MoveFileEx(string source,string target,uint flags);
    static void RemoveTree(string path,string root,string self,bool test) {
        string full=Path.GetFullPath(path),basePath=Path.GetFullPath(root).TrimEnd('\\');
        Need(full.StartsWith(basePath+"\\",StringComparison.OrdinalIgnoreCase),"Remoção fora da instalação recusada.");Plain(full);
        if(Directory.Exists(full)){
            foreach(string p in Directory.GetFileSystemEntries(full))RemoveTree(p,basePath,self,test);
            if(Directory.GetFileSystemEntries(full).Length==0)Directory.Delete(full);
            else{Need(!test&&MoveFileEx(full,null,4),"Não foi possível agendar a remoção da pasta.");}
        }else if(String.Equals(full,self,StringComparison.OrdinalIgnoreCase)||String.Equals(full,self+".config",StringComparison.OrdinalIgnoreCase)){
            Need(!test&&MoveFileEx(full,null,4),"Não foi possível agendar a remoção do desinstalador.");
        }else File.Delete(full);
    }
    static void UninstallCore(string root,bool test) {
        Need(!test||!Administrator(),"O teste de remoção exige usuário comum.");
        if(!test){Need(Administrator(),"A remoção exige autorização do Windows.");root=Path.Combine(ProgramFiles(),"ImperioAION");TreeAcl(root);}
        else{root=Path.GetFullPath(root);PlainAncestors(root);Need(File.Exists(Path.Combine(root,".imperio-local-test")),"Marcador de teste ausente.");}
        // Validate EVERY descendant before deleting any. No links, junctions,
        // arbitrary root argument in production, user data or driver removal.
        var pending=new Stack<string>();pending.Push(root);int count=0;
        while(pending.Count>0)foreach(string p in Directory.GetFileSystemEntries(pending.Pop())){Need(++count<=100000,"Instalação excessiva.");Plain(p);if(Directory.Exists(p))pending.Push(p);}
        string self=test?null:Path.GetFullPath(Process.GetCurrentProcess().MainModule.FileName);
        foreach(string p in Directory.GetFileSystemEntries(root))RemoveTree(p,root,self,test);
        if(Directory.GetFileSystemEntries(root).Length==0)Directory.Delete(root);
        else Need(!test&&MoveFileEx(root,null,4),"A remoção da pasta final não pôde ser agendada.");
        if(!test)Registry.LocalMachine.DeleteSubKeyTree(UninstallKey,false);
        Console.WriteLine("PASS · programa removido · dados pessoais preservados");
    }
    static int Uninstall() {
        if(!Administrator()){
            string own=Process.GetCurrentProcess().MainModule.FileName;
            var start=new ProcessStartInfo(own,"--uninstall-confirmed"){UseShellExecute=true,Verb="runas",WorkingDirectory=Path.GetDirectoryName(own)};
            using(var p=Process.Start(start)){p.WaitForExit();if(p.ExitCode!=0)return p.ExitCode;}
            // Only the original, unelevated account removes its own startup
            // value. Never clean another user's hive under alternate UAC creds.
            using(var key=Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run",true)){
                if(key!=null){string value=key.GetValue("ImperioAION") as string;
                    string root=Path.Combine(ProgramFiles(),"ImperioAION")+"\\";
                    if(value!=null&&value.StartsWith("\""+root,StringComparison.OrdinalIgnoreCase))key.DeleteValue("ImperioAION",false);}
            }
            CleanupLinks();return 0;
        }
        return UninstallConfirmed();
    }
    static void CleanupLinks(){
        // Resolve .lnk target before removing a named current-user shortcut.
        // A same-named unrelated shortcut is preserved.
        Type cls=Type.GetTypeFromProgID("WScript.Shell");if(cls==null)return;object shell=Activator.CreateInstance(cls);
        try{foreach(string folder in new[]{Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),Environment.GetFolderPath(Environment.SpecialFolder.Programs)})
            foreach(string name in new[]{"Império AION.lnk","Império AION · teste.lnk"}){
                string path=Path.Combine(folder,name);if(!File.Exists(path))continue;Plain(path);object link=null;
                try{link=cls.InvokeMember("CreateShortcut",System.Reflection.BindingFlags.InvokeMethod,null,shell,new object[]{path});
                    string target=(string)link.GetType().InvokeMember("TargetPath",System.Reflection.BindingFlags.GetProperty,null,link,null);
                    if(Path.GetFullPath(target).StartsWith(Path.Combine(ProgramFiles(),"ImperioAION")+"\\",StringComparison.OrdinalIgnoreCase))File.Delete(path);
                }finally{if(link!=null)Marshal.ReleaseComObject(link);}
            }
        }finally{Marshal.ReleaseComObject(shell);}
    }
    static int UninstallConfirmed(){
        Need(Administrator(),"A remoção exige autorização do Windows.");
        string state;if(MonitorRunning(out state)){MessageBox.Show("Feche o Império AION pelo X da HUD e tente novamente.","Império AION",MessageBoxButtons.OK,MessageBoxIcon.Information);return 2;}
        if(MessageBox.Show("Remover o Império AION deste computador?\n\nSeus dados, configurações e exportações serão preservados.","Desinstalar Império AION",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)return 3;
        using(var monitor=ClaimClosedMonitor()){
            string root=Path.Combine(ProgramFiles(),"ImperioAION")+"\\";
            string self=Process.GetCurrentProcess().MainModule.FileName;
            foreach(var process in Process.GetProcesses())using(process){
                if(process.Id==Process.GetCurrentProcess().Id)continue;
                string path=null;try{path=process.MainModule.FileName;}catch(System.ComponentModel.Win32Exception){continue;}catch(InvalidOperationException){continue;}
                // The unelevated parent waits for this UAC child and runs from
                // the same protected maintenance executable. Its own files
                // are scheduled for deletion at reboot, so it must be allowed.
                Need(!BlocksUninstall(root,self,path),"Feche o aplicativo e o atualizador antes de desinstalar.");
            }
            UninstallCore(null,false);
        }
        MessageBox.Show("Império AION removido.\n\nReinicie o Windows para concluir a remoção dos arquivos do desinstalador. Seus dados foram preservados.","Império AION",MessageBoxButtons.OK,MessageBoxIcon.Information);return 0;
    }
    static bool BlocksUninstall(string root,string self,string path){
        return path!=null&&path.StartsWith(root.TrimEnd('\\')+"\\",StringComparison.OrdinalIgnoreCase)
            &&!String.Equals(path,self,StringComparison.OrdinalIgnoreCase);
    }
    [STAThread]static int Main(string[] args) {
        Console.OutputEncoding=new UTF8Encoding(false);
        try {
            if(args.Length==4&&args[0]=="--test-uninstall-process"){Console.WriteLine(BlocksUninstall(Path.GetFullPath(args[1]),Path.GetFullPath(args[2]),Path.GetFullPath(args[3]))?"BLOCK":"ALLOW");return 0;}
            if(args.Length==1&&args[0]=="--uninstall")return Uninstall();
            if(args.Length==1&&args[0]=="--uninstall-confirmed"){Need(Administrator(),"Autorize a remoção no Windows.");return UninstallConfirmed();}
            if(args.Length==2&&args[0]=="--test-uninstall"){UninstallCore(args[1],true);return 0;}
            if(args.Length==3&&args[0]=="--test-registration"){Need(Regex.IsMatch(args[2],@"^0\.[0-9]{1,3}\.[0-9]{1,3}$"),"Versão inválida.");Console.WriteLine(Json.Serialize(Registration(Path.GetFullPath(args[1]),args[2])));return 0;}
            if(args.Length==1&&args[0]=="--launch-background"){Launch(true,true,false);return 0;}
            if(args.Length==1&&args[0]=="--launch-capture"){Launch(true,false,true);return 0;}
            if(args.Length==1&&args[0]=="--inspect-installed"){Inspect(null,false);return 0;}
            if(args.Length==2&&args[0]=="--test-inspect-installed"){Inspect(args[1],true);return 0;}
            if(args.Length==1&&args[0]=="--launcher-info"){LauncherInfo(null,false);return 0;}
            if(args.Length==2&&args[0]=="--test-launcher-info"){LauncherInfo(args[1],true);return 0;}
            if(args.Length==1&&(args[0]=="--launch"||args[0]=="--verify-installed")){Launch(args[0]=="--launch");return 0;}
            if(args.Length==1&&args[0]=="--rollback"){Rollback(null,false);return 0;}
            if(args.Length==2&&args[0]=="--test-rollback"){Rollback(args[1],true);return 0;}
            if(args.Length==3&&args[0]=="--verify"){var m=Manifest(ReadSmall(args[2]),true);PlainAncestors(args[1]);using(var f=new FileStream(args[1],FileMode.Open,FileAccess.Read,FileShare.Read)){Need(f.Length==Number(Obj(m["archive"]),"size")&&Hash(f)==Text(Obj(m["archive"]),"sha256"),"ZIP divergente.");}Console.WriteLine("PASS · assinatura RSA e ZIP · "+Text(m,"version"));return 0;}
            if(args.Length==4&&args[0]=="--test-install"){Install(args[1],args[2],args[3],true);return 0;}
            if(args.Length==3&&args[0]=="--install"){Install(args[1],args[2],null,false);return 0;}
            throw new InvalidDataException("Use o imperiobdo.exe para baixar e instalar a atualização.");
        }catch(System.ComponentModel.Win32Exception ex){
            if(ex.NativeErrorCode==1223){Console.Error.WriteLine("PERMISSAO_CANCELADA");return 3;}
            Console.Error.WriteLine("RECUSADO · "+ex.Message);return 2;
        }catch(Exception ex){if(args.Length>0&&args[0].StartsWith("--uninstall",StringComparison.Ordinal))MessageBox.Show(ex.Message,"Não foi possível desinstalar",MessageBoxButtons.OK,MessageBoxIcon.Warning);Console.Error.WriteLine("RECUSADO · "+ex.Message);return 2;}
    }
}
