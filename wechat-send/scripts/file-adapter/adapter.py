"""Pinned WeChat 4.1.15.13 background adapter. Never retries submissions."""
import argparse,ctypes as C,ctypes.wintypes as W,hashlib,json,os,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PROFILE=json.loads((ROOT/'profile.json').read_text(encoding='utf8'))
K=C.WinDLL('kernel32',use_last_error=True)
PTR=C.c_void_p;SIZE=C.c_size_t
class Module(C.Structure):
 _fields_=[('dwSize',W.DWORD),('th32ModuleID',W.DWORD),('th32ProcessID',W.DWORD),('GlblcntUsage',W.DWORD),('ProccntUsage',W.DWORD),('modBaseAddr',PTR),('modBaseSize',W.DWORD),('hModule',PTR),('szModule',W.WCHAR*256),('szExePath',W.WCHAR*260)]
class Process(C.Structure):
 _fields_=[('dwSize',W.DWORD),('cntUsage',W.DWORD),('th32ProcessID',W.DWORD),('th32DefaultHeapID',SIZE),('th32ModuleID',W.DWORD),('cntThreads',W.DWORD),('th32ParentProcessID',W.DWORD),('pcPriClassBase',W.LONG),('dwFlags',W.DWORD),('szExeFile',W.WCHAR*260)]
class Request(C.Structure):
 _fields_=[('size',W.DWORD),('mode',W.DWORD),('status',W.DWORD),('exception',W.DWORD),('base',SIZE),('type',W.DWORD),('reserved',W.DWORD),('receiver',C.c_char*256),('text',C.c_char*3072),('path',W.WCHAR*1024),('file_size',C.c_ulonglong)]
def api(name,ret,*args):
 f=getattr(K,name);f.restype=ret;f.argtypes=args;return f
snapshot=api('CreateToolhelp32Snapshot',PTR,W.DWORD,W.DWORD)
close=api('CloseHandle',W.BOOL,PTR)
pfirst=api('Process32FirstW',W.BOOL,PTR,C.POINTER(Process));pnext=api('Process32NextW',W.BOOL,PTR,C.POINTER(Process))
mfirst=api('Module32FirstW',W.BOOL,PTR,C.POINTER(Module));mnext=api('Module32NextW',W.BOOL,PTR,C.POINTER(Module))
openprocess=api('OpenProcess',PTR,W.DWORD,W.BOOL,W.DWORD)
read=api('ReadProcessMemory',W.BOOL,PTR,PTR,PTR,SIZE,C.POINTER(SIZE))
write=api('WriteProcessMemory',W.BOOL,PTR,PTR,PTR,SIZE,C.POINTER(SIZE))
alloc=api('VirtualAllocEx',PTR,PTR,PTR,SIZE,W.DWORD,W.DWORD)
free=api('VirtualFreeEx',W.BOOL,PTR,PTR,SIZE,W.DWORD)
thread=api('CreateRemoteThread',PTR,PTR,PTR,SIZE,PTR,PTR,W.DWORD,C.POINTER(W.DWORD))
wait=api('WaitForSingleObject',W.DWORD,PTR,W.DWORD)
getexit=api('GetExitCodeThread',W.BOOL,PTR,C.POINTER(W.DWORD))
getmodule=api('GetModuleHandleExW',W.BOOL,W.DWORD,PTR,C.POINTER(PTR))
getname=api('GetModuleFileNameW',W.DWORD,PTR,W.LPWSTR,W.DWORD)
def checked(x):
 if not x:raise C.WinError(C.get_last_error())
 return x
def modules(pid):
 h=checked(snapshot(0x18,pid));out=[]
 try:
  m=Module();m.dwSize=C.sizeof(m);ok=mfirst(h,C.byref(m))
  while ok:
   out.append({'name':m.szModule,'path':m.szExePath,'base':int(m.modBaseAddr),'size':int(m.modBaseSize)})
   ok=mnext(h,C.byref(m))
 finally:close(h)
 return out
def processes():
 h=checked(snapshot(2,0));out=[]
 try:
  p=Process();p.dwSize=C.sizeof(p);ok=pfirst(h,C.byref(p))
  while ok:
   if p.szExeFile.lower()=='weixin.exe':out.append(int(p.th32ProcessID))
   ok=pnext(h,C.byref(p))
 finally:close(h)
 return out
def rpm(h,address,n):
 b=C.create_string_buffer(n);done=SIZE();checked(read(h,address,b,n,C.byref(done)))
 if done.value!=n:raise RuntimeError('short memory read')
 return b.raw
def select(pid):
 out=[]
 for candidate in ([pid] if pid else processes()):
  try:
   ms=modules(candidate);wx=next((m for m in ms if m['name'].lower()=='weixin.dll'),None)
   if wx:out.append((candidate,ms,wx))
  except OSError:continue
 if len(out)!=1:raise RuntimeError('Expected exactly one Weixin.dll process; candidates='+str([p for p,_,_ in out])+'. Use --pid explicitly.')
 return out[0]
def verify(h,wx):
 digest=hashlib.sha256(Path(wx['path']).read_bytes()).hexdigest()
 if digest!=PROFILE['sha256']:raise RuntimeError('Weixin.dll hash mismatch; refusing private native calls')
 for name,sig in PROFILE['signatures'].items():
  expected=bytes.fromhex(sig['bytes'])
  if rpm(h,wx['base']+sig['rva'],len(expected))!=expected:raise RuntimeError('Live signature mismatch: '+name)
 for r,target in [(0x8e534f8,0xaee0),(0x8e53590,0x766c00),(0x9112318,0x19d0bc0),(0x91123f8,0x19d1380),(0x90db0a8,0xaee0),(0x8e53720,0x766c00),(0x8e53728,0x766c10)]:
  if int.from_bytes(rpm(h,wx['base']+r,8),'little')!=wx['base']+target:raise RuntimeError('Live vtable mismatch: '+hex(r))
def remote_call(h,fn,param):
 t=checked(thread(h,None,0,fn,param,0,None))
 try:
  state=wait(t,15000)
  if state!=0:raise RuntimeError('Remote operation did not finish; outcome unknown. Do not retry or unload. Remote buffers retained.')
  result=W.DWORD();checked(getexit(t,C.byref(result)));return result.value
 finally:close(t)
def remote_buffer(h,data):
 addr=checked(alloc(h,None,len(data),0x3000,4));done=SIZE();b=C.create_string_buffer(data)
 try:
  checked(write(h,addr,b,len(data),C.byref(done)))
  if done.value!=len(data):raise RuntimeError('short memory write')
 except Exception:
  free(h,addr,0,0x8000);raise
 return addr
# Resolve a system function through the module that actually owns its address,
# accounting for forwarded kernel32 exports and remote module ASLR.
def remote_system_function(name,ms):
 fn=C.cast(getattr(K,name),PTR).value;owner=PTR();checked(getmodule(6,fn,C.byref(owner)))
 path=C.create_unicode_buffer(32768);checked(getname(owner,path,len(path)))
 remote=next(m for m in ms if m['name'].lower()==Path(path.value).name.lower())
 return remote['base']+fn-owner.value
def execute(h,pid,ms,request):
 dll=ROOT/'wechat-file-send.dll'
 local=C.WinDLL(str(dll));rva=C.cast(local.AdapterExecute,PTR).value-local._handle
 current=next((m for m in ms if Path(m['path']).resolve()==dll.resolve()),None)
 if not current:
  pathdata=(str(dll)+'\0').encode('utf-16-le');buf=remote_buffer(h,pathdata)
  finished=False
  try:
   remote_call(h,remote_system_function('LoadLibraryW',ms),buf);finished=True
  finally:
   if finished:free(h,buf,0,0x8000)
  ms=modules(pid);current=next((m for m in ms if Path(m['path']).resolve()==dll.resolve()),None)
  if not current:raise RuntimeError('Helper DLL failed to load')
 addr=remote_buffer(h,bytes(request));finished=False
 try:
  remote_call(h,current['base']+rva,addr);finished=True
  return Request.from_buffer_copy(rpm(h,addr,C.sizeof(Request)))
 finally:
  if finished:free(h,addr,0,0x8000)
def prepare_file(path):
 path=path.resolve(strict=True)
 if not path.is_file():raise RuntimeError('Expected one regular file')
 size=path.stat().st_size
 if not 0<size<=100*1024*1024:raise RuntimeError('This experimental adapter accepts files of 1 byte to 100 MiB')
 if len(str(path).encode('utf-16-le'))//2>=1024:raise RuntimeError('UTF-16 path limit 1023 code units')
 if '\0' in str(path):raise RuntimeError('NUL path is not supported')
 digest=hashlib.sha256(path.read_bytes()).hexdigest()
 # Native attachment pipeline generates the XML; this field is the filename.
 xml=path.name.encode('utf8')
 if len(xml)>=3072:raise RuntimeError('UTF-8 filename exceeds 3071 bytes')
 return path,size,digest,xml

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['inspect','preflight','probe','send-file']);p.add_argument('--pid',type=int);p.add_argument('--to');p.add_argument('--file',type=Path);p.add_argument('--experimental',action='store_true');a=p.parse_args()
 if C.sizeof(PTR)!=8:raise RuntimeError('64-bit Python required')
 if a.action=='probe':
  path=ROOT/'中文附件构造测试.txt'
  if not path.exists():path.write_text('Codex file object probe. This file has not been sent.\n',encoding='utf8')
 elif a.action!='inspect':
  if not a.file:raise RuntimeError('Specify --file')
  path=a.file
 if a.action!='inspect':path,size,digest,xml=prepare_file(path)
 pid,ms,wx=select(a.pid);h=checked(openprocess(0x410 if a.action in ('inspect','preflight') else 0x43a,False,pid))
 try:
  verify(h,wx)
  if a.action in ('inspect','preflight'):
   out={'status':'live_addresses_verified','pid':pid,'version':PROFILE['version'],'message_sent':False}
   if a.action=='preflight':out.update(file=str(path),file_size=size,file_sha256=digest)
   print(json.dumps(out,ensure_ascii=False));return
  req=Request();req.size=C.sizeof(req);req.mode=1 if a.action=='probe' else 2;req.receiver=b'filehelper';req.text=xml;req.path=str(path);req.file_size=size
  if a.action=='send-file':
   if not a.experimental:raise RuntimeError('Use --experimental for an explicitly authorized live file test')
   proof=json.loads((ROOT/'runtime-probe.json').read_text())
   if proof.get('pid')!=pid or proof.get('sha256')!=PROFILE['sha256'] or proof.get('status')!='object_layout_verified':raise RuntimeError('Run probe for this process first')
   if not a.to or '\0' in a.to or any(ord(c)<33 for c in a.to):raise RuntimeError('Specify exact WeChat ID with --to')
   target=a.to.encode('utf8')
   if not 0<len(target)<256:raise RuntimeError('Target limit 255 UTF-8 bytes')
   req.receiver=target
   # Stable private snapshot: the queue may read the file asynchronously.
   import shutil
   stage=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'wechat2codex/private/send/files'/str(uuid.uuid4())
   stage.mkdir(parents=True);snapshot=stage/path.name;shutil.copyfile(path,snapshot)
   _,snap_size,snap_digest,_=prepare_file(snapshot)
   if snap_size!=size or snap_digest!=digest:raise RuntimeError('File changed while staging; nothing submitted')
   req.path=str(snapshot)
   attempt={'request_id':str(uuid.uuid4()),'to':a.to,'file_name':path.name,'file_size':size,'file_sha256':digest,'staged_file':str(snapshot),'time':time.time(),'status':'submission_started'}
   with (ROOT/'attempts.jsonl').open('a',encoding='utf8') as log:log.write(json.dumps(attempt)+'\n')
  result=execute(h,pid,ms,req)
  out={'status':{2:'object_layout_verified',3:'submitted_unconfirmed'}.get(result.status,'native_error'),'code':result.status,'exception':hex(result.exception),'type':result.type,'subtype':result.reserved,'pid':pid,'sha256':PROFILE['sha256'],'message_sent':False if a.action=='probe' else 'unconfirmed','delivery_verified':False}
  if result.status==2:(ROOT/'runtime-probe.json').write_text(json.dumps(out,indent=2),encoding='utf8')
  if a.action=='send-file':
   out.update(attempt);out['status']='submitted_unconfirmed' if result.status==3 else 'native_error'
   with (ROOT/'attempts.jsonl').open('a',encoding='utf8') as log:log.write(json.dumps(out)+'\n')
  print(json.dumps(out,ensure_ascii=False))
  if result.status not in (2,3):sys.exit(1)
 finally:close(h)
if __name__=='__main__':
 try:main()
 except Exception as e:print(json.dumps({'status':'error','error':str(e)},ensure_ascii=False));sys.exit(1)
