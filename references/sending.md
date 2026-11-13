# 微信后台发送：版本、验证与回执

发送前须完成 [微信skills 的完整草稿与人工审核](../SKILL.md#强制草拟与人工审核)。读取、inspect、preflight、mode=1对象probe不会提交消息，按任务直接执行；发送文字和附件仍逐项审核。

## 支持范围与真实验证程度

- 4.1.15.13：保留原文字与附件组件及精确DLL指纹；历史上有本机发出记录、非零server_id的发送证据。该证据只属于旧版，不转移给新版。
- 4.2.0.13：新增独立组件，11个函数头和7个虚表指针已与本机DLL核对；文字/附件的mode=1对象构造、字段校验、析构探针通过。2026-11-07经用户逐项审核，在一个私聊中完成一次文字和一份9,247,708字节ZIP发送；全文/文件名/大小/上传MD5一致，新记录有非零server_id和附件/CDN标识。未核验接收端下载，未据此宣称群聊或其他场景均已验证。
- 根据实际加载的Weixin.dll完整SHA256选择配置；文件名、版本目录或相近版本号都不能代替指纹匹配。未知指纹保持禁用私有调用，明确输出actual_version、supported_versions及错误码。

读取命令doctor的ready/partial描述数据库读取能力；新增sending.text和sending.file分别报告发送组件的只读检查和probe_status。

## 入口与执行

解释器：%LOCALAPPDATA%/wechat2codex/venv/Scripts/python.exe。
文字：scripts/send/adapter.py；附件：scripts/send/file-adapter/adapter.py。
所有调用继续使用Node child_process.spawn、windowsHide:true、参数数组和捕获输出，不弹终端，不操作微信窗口。

1. inspect：选择当前唯一微信主进程，按完整DLL指纹选择对应helper，核对运行中签名/虚表；多进程时用准确--pid。
2. probe：独立helper构造并释放短文字/附件对象，mode=1在builder和dispatcher之前返回；不会入队。每个进程与helper构建均须有对应成功记录。probe失败或超时后停止，不自动重试、不盲目释放异常中的未知对象。
3. preflight --file <绝对路径>：附件路径、大小和SHA256的只读检查。
4. 完整展示review.py生成的文字或附件草稿，等待用户对准确草稿ID的明确批准。
5. 文字send --to <准确ID> --text-file <UTF-8文件> --draft-id <已批准ID> --experimental；附件send-file --to <准确ID> --file <绝对路径> --draft-id <已批准ID> --experimental。每份批准只提交一次。实际提交前还会从该微信进程打开的db_storage文件核对草稿中的发送账号；账号改变、进程实例改变或无法唯一确定账号时拒绝提交。

probe记录绑定PID、进程创建时间、Weixin.dll指纹、helper构建指纹；重启、升级或重建后应重新验证，旧记录不能直接复用。开始新probe即原子作废旧成功状态，失败或超时不会留下可用旧凭据。helper按版本/构建独立命名，旧DLL自然随微信退出释放，不主动卸载或重启微信。
新运行记录位于%LOCALAPPDATA%/wechat2codex/private/send/runtime/<版本>/<text或file>/；附件probe样本在private/send/probes。此前技能目录中的旧日志不作为新版probe依据。

## 大小与内容

文字为1—3071 UTF-8字节；准确对象ID最多255 UTF-8字节。超限拒绝，不自动拆分或截断。
附件为单个普通文件，1字节—100 MiB，UTF-16本地路径最多1023码元。是本组件限制，不是微信官方上限。
review_file是供用户审核的完整私有快照；发送时再制作哈希一致的异步上传快照。保留文件名，由微信生成XML，不向原生名称字段传入预制XML。
上传结果不明时保留快照，不删、不重试。

## 回执与停止

submitted_unconfirmed只表示原生调用返回，不能写成“已发送成功”。提交后回读准确会话的新记录，核对发送者、时间、全文或附件名称/大小，并区分本机记录和非零server_id等服务端证据。
附件数据库类型为(6 << 32) | 49；核验原生附件/CDN标识是否存在。aeskey、上传密钥只判断存在，不输出值。
本机出现卡片不证明上传完成；附件上传中的占位记录也可能先有非零server_id，上传完成时server_id和XML会更新。应继续只读核对真实附件/CDN标识、文件大小及上传MD5，不重复提交。接收端下载后哈希一致才是端到端内容验证。
提交结果不明、超时或回读失败时停止，不自动重试。批准在native调用前消费，失败也不能重用；先检查回执，再为需要的重发建立新草稿。
DLL哈希、helper哈希或运行签名不符时停止，不替换预期哈希来“放行”，不降级到点击/键盘发送。新版适配按[维护流程](native-version-adaptation.md)完成重新定位。
前台监测只作诊断，不能用焦点变化决定收件人、开始或重试；不读取当前聊天窗口作为目标。
