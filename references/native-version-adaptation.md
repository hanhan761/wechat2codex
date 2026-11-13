# 原生发送版本维护

仅在用户授权维护技能、或排查不支持版本时使用。用户的普通发送请求不授权向额外收件人发送测试消息。

## 需要区分的状态

- 读取ready与发送支持是两件能力；doctor.sending按文字和附件分别报告。
- unsupported_wechat_binary：当前完整指纹未收录，先只读核对已加载DLL路径与版本，避免反复执行同样的旧版inspect。
- native_helper_hash_mismatch：选中的helper发生变化，先检查构建和配置，不能继续调用。
- live_addresses_verified：只读地址核验通过。
- object_layout_verified：当前PID与helper的对象probe通过，仍没有发送证据。
- submitted_unconfirmed、本机新记录、服务端server_id、接收端下载哈希是后续不同层级的证据，分别记录。

## 适配方法

1. 保存旧版源文件、profile与helper，不替换旧SHA256来试探。版本配置和新组件放在scripts/send/versions/<版本>/。
2. 核定当前本机DLL完整SHA256。静态重新定位构造、析构、分配/释放、类型getter、参数builder、dispatcher及相关虚表；不能仅靠函数短前缀相似度选地址。
3. 用完整指令结构、原生调用者、虚表槽位和字段读写互相核对。文字与文件分别核对真实分配大小；4.2.0.13文本wrapper为0x7a8，文件原生共享构造自分配0x768。
4. 新profile同时记录签名、指针检查、字段布局、helper文件名及SHA256。按完整DLL指纹选择；native和Python层都保留运行签名检查。
5. 只读inspect通过后，先做独立静态审查，再各一次短输入、mode=1的对象构造/字段/释放probe。任何失败后停止，不自动反复probe；异常中的对象状态不明时不盲目析构。
6. 真实提交只使用用户已选定的收件人/内容，经完整草稿和人工批准后一次执行；不把旧版发送证据、当前对象probe或“已提交”当作新版已送达。

4.2.0.13的静态字段与调用关系摘要保存在版本 profile 的 evidence 中。不能直接执行历史发送试验。

## 构建与回归

保留build.py默认的旧LLVM+SDK构建入口。新版可用已安装的Zig（MSVC ABI）：

    python scripts/send/build.py --version 4.2.0.13 --zig <zig.exe绝对路径>

Zig通过最小kernel32.def生成导入库，无需Visual Studio。编译子进程均使用CREATE_NO_WINDOW；缓存和中间文件在private/build/<版本>/。helper按构建指纹命名，重建后旧probe失效。

离线检查：

    python -m pytest scripts/send/tests scripts/test_forwarded.py
    python <skill-creator>/scripts/quick_validate.py <wechat-skills目录>

测试不得对真实会话提交消息。必须验证：同版本目录内未知哈希仍拒绝；helper变化会拒绝；PID/进程创建时间/二进制/helper不一致的旧probe不生效；失败probe作废旧成功状态；当前打开数据库的账号必须与草稿一致；未经批准的send入口到不了进程选择；健康检查只调用inspect并隐藏窗口。
