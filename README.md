# WeChat Agent（Windows / macOS）

本地微信桌面助手：截图识别会话 → 大模型生成回复 → Unicode 逐字输入并发送。支持人工审核和未读会话自动回复两种模式。

## 运行

安装包见 [GitHub Releases](https://github.com/muzi86754781/rpa-wx/releases/latest)：Windows 10/11 x64 安装程序、macOS 13+ Apple Silicon 和 Intel DMG。安装包不需要单独安装 Python。macOS 请先把应用拖入 Applications，并在系统设置中授予“辅助功能”和“屏幕录制”权限，授权后重启。当前无开发者证书签名/公证，详情见 DMG 内说明。

从源代码运行需要 Windows 10/11 或 macOS 13+、Python 3.11+ 和已登录的桌面微信。macOS 推荐 python.org 官方 Python（包含 Tcl/Tk），命令使用 `python3`。

```powershell
python -m pip install -r requirements.txt
python app.py
```

也可双击 `start.cmd`。API Key 在界面填写，或通过 `OPENAI_API_KEY` 环境变量提供；不会写入磁盘。默认模型为 `gpt-6.1-terra`，账户必须有对应 API 权限与余额。

## 使用

1. 打开并登录桌面微信，显示聊天列表。点击“绑定窗口”，助手自动查找微信主窗口，无需在倒计时内切换前台。macOS 按应用进程识别微信 / WeChat / Weixin，不依赖窗口标题；不支持企业微信。
2. 点击“校准输入框”，倒计时内将鼠标停在输入框内；同样校准“发送按钮”。此后不要移动或缩放微信窗口。
3. 人工模式：打开目标聊天，点击“识别最新消息”。只读取当前会话，不读取后台数据库。
4. 审核、编辑草稿。确认微信输入框为空，且微信设置使用 Enter 发送、Shift+Enter 换行，再勾选确认。
5. 点击“审核通过”。程序切回微信，再次识别标题与最新消息，逐字输入，重新核对会话后点击发送。输入期间不要操作电脑。
6. 停止按钮取消后续动作；输入过程中按住 Esc 也可中止。部分输入可能留在微信输入框，请手动处理。

监控开关每十秒检查一次，仅微信在前台时生效。已有草稿时暂停识别，发送仍须逐条审核。清空编辑区后再次点识别可释放旧草稿，再点一次开始重新识别。

### 自动回复

完成绑定、两项校准并填写 API Key 后，点击“开始自动回复”。无需逐条审核，也无需勾选人工发送的确认框。启动前请先处理或清空助手和微信中的已有草稿，设置微信为 Enter 发送、Shift+Enter 换行。

- 每 5 秒尝试一次检查（上一轮 API 请求未完成时不会并发）。程序会主动切回微信，先处理当前聊天最后一批对方消息，再从列表查找带红色未读标记的会话，打开并核对名称后生成回复、逐字输入、复核并点击发送。
- 无可回复消息时向下滚动会话列表，检查可见范围之外的未读会话。到达重复页面或最多 50 页后滚回顶部，继续下一轮。未读会话名称被截断、未读标记不明确或识别置信度不足时不会强行点击；请加宽列表或手动打开聊天。
- 会处理启动前已存在的未读消息及当前聊天最后一批尚未处理的对方消息。相同会话、相同文字在本次运行内只处理一次。
- 微信需处于已登录、未最小化的当前桌面，电脑保持唤醒。自动模式会占用鼠标、键盘和前台窗口，运行时请暂停其他电脑操作。
- 发现已有草稿、窗口位置变化、焦点被抢占、会话不匹配或输入内容复核失败时自动停止，保留现场供手动处理。输入前收到新消息也会停止，避免回复过期内容；检查后可重新开启。
- 点击“停止自动回复”或“停止”结束运行；输入时按住 Esc 可中止。正在进行的网络请求结束前不会启动下一轮。

### API 报错排查

HTTP 429 可能表示额度不足，也可能表示临时限流；程序现在会读取结构化错误码并显示中文原因，不显示原始服务器错误正文或密钥。

- `insufficient_quota` / `credit_balance_exhausted`：检查 [API Billing](https://platform.openai.com/account/billing/overview) 中的余额。ChatGPT Plus 本身不包含 API 额度；符合条件的 Pro / Business 领取规则见 [官方说明](https://developers.openai.com/api/docs/guides/chatgpt-api-credits)。密钥页面的 Monthly spend 是消费金额，不是余额。
- `project_spend_limit_exceeded` / `organization_spend_limit_exceeded`：检查对应项目或组织的消费限额。额度、账单或用量上限报错不会自动重试。
- `rate_limit_exceeded` / `slow_down`：暂时达到请求或 Token 速率限制。程序遵守 Retry-After，未提供时使用递增等待。识别及输入前的自动流程最多重试两次，每次重新截图，不复用旧坐标；等待期间可点击停止。
- 冷却超过 120 秒时停止自动重试，提示剩余等待时间；不会提前请求。冷却期在本次应用运行内共享，连续点击不会绕过等待。
- 人工发送、已经开始输入的自动发送遇到限流不会重放。请检查微信中保留的草稿后再继续。服务端未提供可识别错误码的 429 也不会盲目重试。

详情见 [OpenAI 错误说明](https://developers.openai.com/api/docs/guides/error-codes)。

## 边界与实现

- 支持人工审核和自动发送。平滑鼠标使用加减速插值，输入按 Unicode 字符逐个注入；没有规避检测、随机错误、假装真人或反风控功能，也无法承诺微信不会识别自动化。
- 截图包含整个微信窗口，将通过 HTTPS 发给 OpenAI。接口设置 `store=false`，并不代表服务端零保留；请自行选择适合上传的会话。密钥与聊天记录仅存内存；macOS 截图短暂写入临时目录，读取后立即删除，异常进程终止可能留下临时文件。
- 视觉结果可能出错。当前版本依赖标题、消息内容与置信度复核，没有可靠消息 ID；同一会话再次出现完全相同文字会被保守跳过。重启清空去重记录。
- 微信必须可见、无遮挡。窗口移动、最小化、前台焦点变化、低置信度或消息改变会停止操作。窗口坐标校验无法保证识别所有遮挡，使用时应保持微信完整可见。
- 人工模式需确认输入框为空，两种模式均通过截图复核空输入框及输入后的草稿，但视觉复核仍可能出错。不能自动证明点击后送达，界面明确记录“已点击发送”；不要在状态不明时重复发送。
- API 请求最长等待 90 秒；停止不打断网络请求，但请求返回后不继续发送。连续截图识别会产生 API 费用。

## 验证

```powershell
python -m unittest -v
python -m compileall -q app.py core.py api_errors.py automation.py desktop.py desktop_windows.py desktop_macos.py
```

测试覆盖后台微信绑定、主窗口选择、Retina 坐标、自动切换会话和回复、草稿保护、消息变化、取消及去重。真实微信识别、中文输入与发送需要在目标机器配置 API Key 后验收。

## CI 与发布

推送 main 或提交 PR 自动执行 Windows、macOS Apple Silicon / Intel 三平台测试、打包和成品启动检查。macOS 使用 python.org 官方 universal2 Python，部署目标为 13.0，并检查打包后二进制的最低系统版本。该静态检查不能替代 macOS 13 真机验收。

推送 `v0.1.0` 格式标签后，三个平台全部成功才创建 GitHub Release，上传 Windows 安装程序、两种 DMG 及 SHA256SUMS.txt。Release 使用 Actions 自带 GITHUB_TOKEN，不需要把个人令牌或 OpenAI Key 放入仓库 Secrets。

本地打包：安装 `requirements-build.txt` 后运行 `python scripts/build.py --version 0.2.1 --arch x86_64`。Windows 需 Inno Setup 6；macOS Apple Silicon 使用 `--arch arm64`。各平台须在对应系统构建。

官方资料：[Terra 模型](https://developers.openai.com/api/docs/models/gpt-6.1-terra)、[图像输入](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses)。
