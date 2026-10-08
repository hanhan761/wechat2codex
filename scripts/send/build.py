"""Rebuild x64 adapters without visible child windows."""
import argparse,os,subprocess
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--llvm',type=Path,default=Path('C:/Program Files/LLVM/bin'));p.add_argument('--sdk-lib',type=Path);a=p.parse_args()
 if os.name!='nt':p.error('Windows x64 and Windows SDK are required')
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
