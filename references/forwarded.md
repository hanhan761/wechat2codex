# 合并转发与嵌套附件

## 读取

先按准确会话 ID 调用 read；合并转发卡片的 message.forwarded 是有序数组。每个条目包含 item_id、datatype、type、sourcename、sourcetime、datadesc 和 datatitle。内层转发位于该条目的 forwarded，条目 ID 例如 2.1，不展开成重复的平铺记录。

图片、文件、语音和视频条目含 media 的类型及允许公开的定位信息。CDN 下载地址、传输解密密钥和完整原始 XML 不作为结构化输出。forwarded_status 为 parsed、truncated、unparsed 或 rejected；最多解析 5 层、合计 500 条，超出时明确标记。

后台调用统一输出 UTF-8。同一账号的后端调用应顺序执行，避免同时写入读取材料；读取不会自动完成或确认消息。

## 附件入口

~~~text
scripts/wechat.py media --id <父消息id>
scripts/wechat.py media --id <父消息id> --item 2
scripts/wechat.py media --id <父消息id> --item 1.3
~~~

不带 --item 时列出全部转发附件，返回 status: forwarded、items、summary、availability 和 unresolved_records。只有 items 中 status: available 的附件才有可读取路径。带 --item 时只返回该条目；条目 ID 来自 read 的顺序，不是原始数据库 local_id。

- available：已找到并验证本地副本，复制到受限的 private/attachments/<父消息id>/forwarded/<条目id>/。
- local_missing：没有可验证的本地副本；不是“转发不支持”，也不是“附件没有文件名”。需要微信先加载对应转发及图片后重试。
- ambiguous：同一定位信息命中内容不同的副本，或父消息存在跨分片冲突；不能任意选择。
- decode_failed：匹配的本地图片解码失败，返回错误类型，不输出可能带密钥的异常正文。
- unparsed / rejected / truncated：记录解析不完整；列明无法读取的范围。

## 本地恢复依据

只扫描所选账号的 msg/attach、msg/file、cache、resource、temp。按转发媒体的 data_id/MD5 定位；缓存使用不透明文件名时，按原始长度筛选并核对内容 MD5。普通转发文件缺少哈希时，要求文件名、大小、父消息月份一致。没有证据就返回缺失，不以时间接近、图片外观或相邻消息猜配。

原始消息 local_id、sort_seq、时间及类型要通过冲突检查。已读取的旧记录可直接使用 media：入口重新解码准确源行；再次 read 会更新结构化内容而保留处理回执。

图片保持真实字节；本地微信加密 .dat 及 Rec/<记录>/Img/<数字>、<数字>_t 无扩展名缓存使用固定后端解码。V1/V2 头部长度与 AES 填充仅用于筛选候选，最终必须核对解密后内容 MD5，不按数字文件名或大小猜配。转发 fullmd5 可能包含原始容器尾部，校验前保留这些字节，不能因后端裁掉尾部而把原图误报为缺失。quality 区分 original、preview、thumbnail、unknown。完整内容哈希验证通过才标记 original；无法证明完整原图时不能当作原图报告。保留图像尺寸和 SHA256。wxgf 容器先校验原始内容再转 JPEG 预览，保留独立 source_path、source_sha256、source_quality 与 conversion；预览 quality 不标原图。读取脚本不请求 CDN，也不自行操作界面。

## 缓存缺失时的智能体恢复流程

用户已请求读取本机微信附件时，加载并查看该附件属于读取范围。不要立即要求用户手动打开：

1. 阅读并使用已安装的 computer-use 技能，通过其支持的窗口 API 选择唯一微信主窗口；禁止借用发送 DLL 或自制 UI 自动化脚本。
2. 按 read 返回的准确群名、父卡片标题、发送者、时间及前后文字定位卡片。搜索框与消息输入框必须区分；只搜索和打开，不发送。
3. 打开准确的转发记录，再按条目顺序查看目标图片。每次输入后刷新窗口状态；预览窗出现时选择新返回的窗口，不能把其他已有预览当作目标。
4. 加载后顺序重跑 media --id；仍缺失时再检查保存/下载按钮，将附件保存到 private 内，并使用原始 MD5 验证。不得仅靠外观、时间或相邻位置配图。
5. 若界面能力不可用、未登录、桌面锁定或无法唯一定位，报告实际原因及剩余范围；不能把工具不可用与转发解析不支持混为一谈。

该流程不授予消息或附件发送权限，也不要求用户为普通只读加载重复批准。

## 验证

~~~text
python scripts/test_forwarded.py
~~~

回归检查使用合成消息和临时本地缓存，不发送微信、不运行附件中的代码。真实消息验证应报告已识别条目与实际附件可用性，不能用合成测试通过冒充真实附件已恢复。
