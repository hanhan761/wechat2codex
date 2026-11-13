"""Rebuild x64 adapters without visible child windows."""
import argparse,os,subprocess,json,hashlib,shutil
from pathlib import Path

def build_version(args):
 root=Path(__file__).resolve().parent
 folder=root/'versions'/args.version
 profile=json.loads((folder/'profile.json').read_text(encoding='utf8'))
 zig=args.zig or Path.home()/'.codex/packages/wechat-native-toolchain/zig-0.13.0/ziglang/zig.exe'
 if not zig.is_file():raise RuntimeError('Specify --zig with an installed Zig compiler path')
 cache=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'wechat2codex/private/build'/args.version
 cache.mkdir(parents=True,exist_ok=True)
 env=os.environ.copy();env['ZIG_GLOBAL_CACHE_DIR']=str(cache/'cache');env['ZIG_LOCAL_CACHE_DIR']=str(cache/'local-cache')
 def execute(command):
  subprocess.run([str(x) for x in command],check=True,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
 definition=cache/'kernel32.def';library=cache/'kernel32.lib'
 definition.write_text('LIBRARY kernel32.dll\nEXPORTS\nGetModuleHandleW\nGetProcAddress\nGetProcessHeap\nHeapFree\n',encoding='ascii')
 execute([zig,'dlltool','-d',definition,'-l',library,'-m','i386:x86-64'])
 for kind in ['text','file']:
  output=cache/(kind+'-native-build.dll')
  execute([zig,'cc','-target','x86_64-windows-msvc','-shared','-O1','-fms-extensions','-fno-builtin','-fno-stack-protector','-nostdlib','-Wl,--entry=DllMain',folder/(kind+'-native.c'),library,'-o',output])
  digest=hashlib.sha256(output.read_bytes()).hexdigest()
  name=('wechat-send-' if kind=='text' else 'wechat-file-send-')+args.version+'-'+digest[:12]+'.dll'
  target=folder/name
  if not target.exists():shutil.copyfile(output,target)
  elif hashlib.sha256(target.read_bytes()).hexdigest()!=digest:raise RuntimeError('Existing build name has different contents')
  profile['helpers'][kind]=name;profile.setdefault('helper_sha256',{})[kind]=digest
 (folder/'profile.json').write_text(json.dumps(profile,indent=2),encoding='utf8')
 print(json.dumps({'version':args.version,'helpers':profile['helpers'],'helper_sha256':profile['helper_sha256'],'probe_required':True}))

def main():
 p=argparse.ArgumentParser();p.add_argument('--llvm',type=Path,default=Path('C:/Program Files/LLVM/bin'));p.add_argument('--sdk-lib',type=Path);p.add_argument('--version');p.add_argument('--zig',type=Path);a=p.parse_args()
 if os.name!='nt':p.error('Windows x64 is required')
 if a.version:
  if not __import__('re').fullmatch(r'\d+\.\d+\.\d+\.\d+',a.version):p.error('Expected a four-part version')
  return build_version(a)
 sdk=a.sdk_lib
 if sdk is None:
  root=Path('C:/Program Files (x86)/Windows Kits/10/Lib')
  candidates=sorted((x/'um/x64' for x in root.iterdir() if (x/'um/x64/kernel32.lib').is_file()),key=lambda x:tuple(int(v) for v in x.parents[1].name.split('.')))
  if not candidates:p.error('kernel32.lib not found; specify --sdk-lib')
  sdk=candidates[-1]
 for folder,name in [(Path(__file__).resolve().parent,'wechat-send'),(Path(__file__).resolve().parent/'file-adapter','wechat-file-send')]:
  obj=folder/'native.obj'
  subprocess.run([str(a.llvm/'clang.exe'),'--target=x86_64-pc-windows-msvc','-c','-O1','-fno-builtin','-fno-stack-protector',str(folder/'native.c'),'-o',str(obj)],check=True,creationflags=subprocess.CREATE_NO_WINDOW)
  subprocess.run([str(a.llvm/'lld-link.exe'),'/dll','/entry:DllMain','/nodefaultlib','/machine:x64','/out:'+str(folder/(name+'.dll')),str(obj),'/libpath:'+str(sdk),'kernel32.lib'],check=True,creationflags=subprocess.CREATE_NO_WINDOW)
  print('Built '+name+'.dll')
if __name__=='__main__':main()
