/* Minimal Win32 ABI declarations: no CRT or Visual Studio runtime needed. */
typedef unsigned long DWORD;typedef int BOOL;typedef unsigned long long SIZE_T,ULONG_PTR;typedef void* HINSTANCE;typedef void* LPVOID;
#define WINAPI __stdcall
#define TRUE 1
#define EXCEPTION_EXECUTE_HANDLER 1
unsigned long _exception_code(void);
#define GetExceptionCode _exception_code
__declspec(dllimport) void* __stdcall GetModuleHandleW(const unsigned short*);
__declspec(dllimport) void* __stdcall GetProcessHeap(void);
__declspec(dllimport) BOOL __stdcall HeapFree(void*,DWORD,void*);
#include "profile.h"
__declspec(dllimport) void* __stdcall GetProcAddress(void*,const char*);
typedef int (*SehHandler)(void*,void*,void*,void*);
static SehHandler seh_handler;
int __C_specific_handler(void* a,void* b,void* c,void* d){return seh_handler?seh_handler(a,b,c,d):1;}
typedef unsigned long long U64;
typedef struct {DWORD size,mode,status,exception; ULONG_PTR base; DWORD type,reserved; char receiver[256]; char text[3072];} Request;
typedef void* (*Alloc)(SIZE_T);
typedef void (*Free)(void*);
typedef void* (*Ctor)(void*);
typedef void* (*Builder)(void*,void*,void*,void*,void*,U64);
typedef void (*Dispatch)(void*,void*);
static void zero(void* p,SIZE_T n){unsigned char* b=p;while(n--)*b++=0;}
static void copy(void* a,const void* b,SIZE_T n){unsigned char* x=a;const unsigned char* y=b;while(n--)*x++=*y++;}
static SIZE_T length(const char* p,SIZE_T max){SIZE_T i=0;while(i<max&&p[i])i++;return i;}
static int equal(const void* a,const void* b,SIZE_T n){const unsigned char* x=a;const unsigned char* y=b;while(n--)if(*x++!=*y++)return 0;return 1;}
/* MSVC std::_Func_base slots: copy, move, invoke, target_type, delete, target.
   These stateless callbacks have no captures, GUI work, or reply behaviour. */
static void* callbacks[6];
static void* clone(void* self,void* dst){(void)self;*(void***)dst=callbacks;return dst;}
static void invoke(void* self,void* a,void* b,void* c){(void)self;(void)a;(void)b;(void)c;}
static void* target_type(void* self){(void)self;return 0;}
static void destroy(void* self,unsigned char del){if(del)HeapFree(GetProcessHeap(),0,self);}
static void* target(void* self){return (char*)self+8;}
static int validate(unsigned char* base){
 SIZE_T i;
 for(i=0;i<sizeof(signatures)/sizeof(signatures[0]);i++)
  if(!equal(base+signatures[i].rva,signatures[i].bytes,16))return 0;
 if(*(ULONG_PTR*)(base+0x8e534f8)!= (ULONG_PTR)(base+0xaee0))return 0;
 if(*(ULONG_PTR*)(base+0x8e53590)!= (ULONG_PTR)(base+0x766c00))return 0;
 if(*(ULONG_PTR*)(base+0x9112318)!= (ULONG_PTR)(base+0x19d0bc0))return 0;
 if(*(ULONG_PTR*)(base+0x91123f8)!= (ULONG_PTR)(base+0x19d1380))return 0;
 return 1;
}
static int set_string(unsigned char* base,void* dst,const char* src,SIZE_T n){
 U64* d=dst;zero(dst,32);d[2]=n;d[3]=15;
 if(n<16){copy(dst,src,n);return 1;}
 SIZE_T cap=n|15;
 char* buffer=((Alloc)(base+0x74ab89c))(cap+1);
 if(!buffer)return 0;
 copy(buffer,src,n);buffer[n]=0;d[0]=(U64)buffer;d[3]=cap;return 1;
}
__declspec(dllexport) DWORD WINAPI AdapterExecute(Request* req){
 unsigned char* base;unsigned char* wrapper=0;unsigned char* msg;
 SIZE_T rn,tn;U64 data[2],arg1[4],arg2[29],cb1[8],cb2[8],cb3[8],empty[2];
 if(!req||req->size!=sizeof(Request))return 100;
 req->status=100;req->exception=0;
 __try{
  base=(unsigned char*)GetModuleHandleW(L"Weixin.dll");req->base=(ULONG_PTR)base;
  if(!base){req->status=101;return 101;}
  if(!validate(base)){req->status=102;return 102;}
  if(req->mode==0){req->status=1;return 1;}
  if(req->mode!=1&&req->mode!=2){req->status=103;return 103;}
  rn=length(req->receiver,sizeof(req->receiver));tn=length(req->text,sizeof(req->text));
  if(!rn||rn==sizeof(req->receiver)||!tn||tn==sizeof(req->text)){req->status=104;return 104;}
  wrapper=((Alloc)(base+0x74ab89c))(0x7a8);
  if(!wrapper){req->status=105;return 105;}
  zero(wrapper,0x7a8);*(U64*)wrapper=(U64)(base+0x8e534f8);*(U64*)(wrapper+8)=0x200000005ULL;
  msg=wrapper+16;((Ctor)(base+0x766680))(msg);
  if(*(U64*)msg!=(U64)(base+0x8e53588)){req->status=106;return 106;}
  if(!set_string(base,msg+0xb0,req->receiver,rn)||!set_string(base,msg+0x758,req->text,tn)){req->status=105;return 105;}
  *(DWORD*)(msg+0x118)=1;*(U64*)(msg+0x1c8)=tn;
  req->type=((DWORD(*)(void*))(base+0x766c00))(msg);
  if(req->type!=1){req->status=107;return 107;}
  if(req->mode==1){
   ((Ctor)(base+0x7679b0))(msg);((Free)(base+0x74ab8d8))(wrapper);
   req->status=2;return 2;
  }
  zero(cb1,sizeof(cb1));zero(cb2,sizeof(cb2));zero(cb3,sizeof(cb3));zero(arg2,sizeof(arg2));zero(empty,sizeof(empty));
  callbacks[0]=clone;callbacks[1]=clone;callbacks[2]=invoke;callbacks[3]=target_type;callbacks[4]=destroy;callbacks[5]=target;
  cb1[0]=cb2[0]=cb3[0]=(U64)callbacks;cb1[7]=(U64)cb1;cb2[7]=(U64)cb2;cb3[7]=(U64)cb3;
  ((Builder)(base+0xf910))(arg2,cb1,cb2,cb3,empty,*(U64*)(base+0xb0bc2b0));
  data[0]=(U64)msg;data[1]=(U64)wrapper;
  arg1[0]=(U64)(base+0x9112308);arg1[1]=(U64)data;arg1[2]=arg1[3]=(U64)(data+2);
  ((Dispatch)(base+0x19d0bc0))(arg1,arg2);
  /* Async native code owns a copy of the context. Keep the original wrapper alive
     until process exit; never free a buffer the queue may still reference. */
  req->status=3;return 3;
 }__except(EXCEPTION_EXECUTE_HANDLER){req->exception=GetExceptionCode();req->status=108;return 108;}
}
BOOL WINAPI DllMain(HINSTANCE h,DWORD reason,LPVOID reserved){(void)h;(void)reserved;if(reason==1)seh_handler=(SehHandler)GetProcAddress(GetModuleHandleW(L"ntdll.dll"),"__C_specific_handler");return seh_handler!=0;}
