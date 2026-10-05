# wechat2codex

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
程序不发送消息，不操作微信界面。只覆盖本机已同步聊天，未下载附件会返回 local_missing；重复候选返回 ambiguous。
合并转发提取文本和来源，嵌套媒体可能不完整。微信升级可能需要更新后端。
这不是常驻服务，Codex调用skill时才读取处理。

测试：python -m unittest discover -s tests -v
详细来源见 [references/backend.md](references/backend.md)。
