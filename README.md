# wechat2codex

![wechat2codex：将微信私聊、群消息和本地文件转换为摘要、待办与数据分析成果](assets/wechat2codex-intro.png)

让 Codex 在你说“微信”时读取本机微信收到的内容，按你的任务处理。支持私聊、群消息、引用、合并转发，以及本地已有的群文件、图片、语音和视频。

后端采用 [wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica) 1.2.4.4 的只读数据库与媒体模块。运行于 Windows 桌面微信，不要求手动转发给 Codex。

## 安装

把本仓库的 SKILL.md、agents、scripts、references 和 requirements-read.txt 复制到 ~/.codex/skills/wechat2codex，然后运行：

~~~powershell
python scripts/setup.py
& "$env:LOCALAPPDATA/wechat2codex/venv/Scripts/python.exe" scripts/wechat.py doctor
~~~

在 Codex 新会话中说：
- “微信，帮我整理某某群今天的讨论和待办。”
- “微信，把老师发的 Excel 下载下来分析。”
- “微信，看看有哪些未读群消息。”

CLI 示例：

~~~powershell
$py = "$env:LOCALAPPDATA/wechat2codex/venv/Scripts/python.exe"
& $py scripts/wechat.py chats --groups
& $py scripts/wechat.py read --chat "准确群ID@chatroom" --limit 50 --pending
& $py scripts/wechat.py media --id "上一条输出中的消息id"
& $py scripts/wechat.py ack --id "已完成的消息id" --result "产物路径或完成说明"
~~~

读取不会自动写成功回执。处理和验证成功后才 ack，避免重复处理。

## 数据与限制

消息、附件、解密副本、读取材料与回执只保存在 %LOCALAPPDATA%/wechat2codex/private，并限制 Windows ACL；不要把它们提交 GitHub。
读取 skill 不发送消息，不操作微信界面。可选的独立发送 skill 见下文。只覆盖本机已同步聊天，未下载附件会返回 local_missing；重复候选返回 ambiguous。
合并转发提取文本和来源，嵌套媒体可能不完整。微信升级可能需要更新后端。
这不是常驻服务，Codex调用skill时才读取处理。

测试：python -m unittest discover -s tests -v
详细来源见 [references/backend.md](references/backend.md)。

## 可选：后台发送文本与文件

[wechat-send](wechat-send/SKILL.md) 是独立发送 skill。把本仓库的整个 wechat-send 目录复制到 ~/.codex/skills/wechat-send，并先按上面的步骤安装读取后端及 Python 环境。之后可以说“给某人发微信：原文”或“把这个本地文件发到某个群”。发送只在用户指定对象和内容时执行，不提供自动回复。

当前发送组件固定适配 Windows x64 微信 **4.1.15.13**，校验 Weixin.dll SHA256、运行函数签名和虚函数表；不匹配会拒绝调用。通过原生发送队列提交，不点击界面、不模拟按键、不选择当前聊天窗口，不更换微信版本。前台窗口切换不作为发送条件。

本机已实测一次群文本发送及发到文件传输助手的普通附件上传。修正版附件的中文文件名、55字节大小、上传MD5和非零服务端消息编号均核对通过；用户切换应用期间仍正常上传。仅55字节TXT做过文件实测，大文件、其他格式及群文件尚未单独验证，也没有接收端下载后的端到端哈希证据。

文件接口目前接受1字节到100MiB、UTF-16路径最多1023码元的普通文件；这是组件限制。发送前建立哈希一致的私有快照，避免异步上传期间原文件变化。运行回执、快照和读取材料不上传GitHub。提交结果不明时不自动重试。

仓库提供可编译源码和已构建的原生DLL。重建方法见 [wechat-send/README.md](wechat-send/README.md)。
