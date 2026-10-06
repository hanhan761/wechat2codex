# 微信后台发送组件

安装与使用见 [SKILL.md](SKILL.md)。本目录包含独立文本组件及 scripts/file-adapter 文件组件。Python加载器逐次验证当前进程；构造探针不会发送消息。

文件名以UTF-8传递，本地路径以UTF-16传递，微信原生附件任务生成XML并上传。不要把XML当作名称参数。发送按准确会话ID执行，不依赖前台窗口。

## 重建

需要64位Windows、LLVM clang/lld和Windows SDK的x64 kernel32.lib。C源码使用最小Win32声明和SEH，不依赖MSVC CRT或Visual Studio头文件。

```powershell
python scripts/build.py
```

可以通过 --llvm 指定 LLVM/bin，通过 --sdk-lib 指定 Windows Kits/10/Lib/<版本>/um/x64。已被微信加载的DLL不能在使用期间覆盖或卸载；等待该微信进程自然退出，或使用新的副本目录。不要为重建主动终止微信。DLL只导入kernel32，不包含鼠标、键盘或窗口激活API。

## 验证范围

4.1.15.13上已验证文本发送及55字节中文名TXT附件上传、服务端回执和上传MD5。首轮文件试验名称参数错误，修正后通过第二次实测。未单独验证大文件、群文件和其他格式；不声称接收方已读或接收端下载哈希已验证。--experimental 仍保留，以标明固定版本私有接口的状态。

研究参考：[WeChat-Hook](https://github.com/aixed/WeChat-Hook)、[WechatHook4.1.10.27](https://github.com/mosheng20205/WechatHook4.1.10.27)。不附带微信安装程序、Weixin.dll、聊天内容、账号密钥或测试回执。
