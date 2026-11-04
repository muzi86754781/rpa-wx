# WeChat Agent（Windows / macOS）

本地微信桌面助手：截图识别会话 → 大模型生成回复 → Unicode 逐字输入并发送。支持人工审核和未读会话自动回复两种模式。

## 运行

安装包见 [GitHub Releases](https://github.com/muzi86754781/rpa-wx/releases/latest)：Windows 10/11 x64 安装程序、macOS 13+ Apple Silicon 和 Intel DMG。安装包不需要单独安装 Python。macOS 请先把应用拖入 Applications，并在系统设置中授予“辅助功能”和“屏幕录制”权限，授权后重启。当前无开发者证书签名/公证，详情见 DMG 内说明。

从源代码运行需要 Windows 10/11 或 macOS 13+、Python 3.11+ 和已登录的桌面微信。macOS 推荐 python.org 官方 Python（包含 Tcl/Tk），命令使用 `python3`。

```powershell
python -m pip install -r requirements.txt
python app.py
```

也可双击 `start.cmd`。点击主界面右上角“设置”配置模型服务。API Key 仅保留在本次运行的内存中，不会写入磁盘；OpenAI 可从 `OPENAI_API_KEY` 环境变量读取，北京百炼可从 `DASHSCOPE_API_KEY` 读取。账户需要有所选模型的访问权限及可用额度。

## 模型服务设置

主界面的右上角“设置”中可选择 OpenAI、阿里云百炼（北京 / 新加坡 / 美国）或自定义 OpenAI 兼容服务。主界面只保留回复要求和微信操作。

- **API Key**：填写当前服务商、当前地域的密钥。切换服务商或地域不会复制上一家的 Key；退出后需重新填写或通过对应环境变量提供。
- **Base URL**：预填服务商地址，也可改为控制台提供的业务空间专属地址。填写接口根路径，不包含 `/chat/completions` 或 `/responses`。云端使用 HTTPS，本机兼容服务可使用 localhost / 127.0.0.1 的 HTTP 地址。
- **截图识别模型**：必须支持图像输入。百炼预填 `qwen3-vl-flash`，也可选择 `qwen3-vl-plus` 或填写其他视觉模型。
- **回复模型**：可以使用文本模型。百炼预填 `qwen-plus`；OpenAI 默认两项均为 `gpt-6.1-terra`。两项使用当前服务商的同一个 Key 和 Base URL。
- **接口协议 / 识别结果格式**：OpenAI 默认 Responses + JSON Schema；百炼默认 Chat Completions + JSON Object。自定义兼容服务支持 JSON Schema、JSON Object 或仅通过提示词要求 JSON；返回结果始终经过本地结构验证，不能识别时停止操作。

选择服务商后填写并点击保存，当前配置立即生效。更换模型前先停止正在运行的识别或自动回复。各服务商模型和地址分别保存，密钥不落盘；冷却状态也按服务商地址和密钥分别管理。

设置文件仅包含服务商、接入地址、模型及接口选项：macOS 为 `~/Library/Application Support/RpaWx/settings.json`，Windows 为 `%APPDATA%/RpaWx/settings.json`。设置中的“服务商控制台”和“百炼免费额度说明”可直接打开官方页面。

### 完整百炼模型目录

内置 2026-11-04 官方模型总览、模型定价及分类文档中整理出的 518 个模型 ID，含正式版、历史及快照版本。覆盖千问、DeepSeek、Kimi、GLM、MiniMax、MiMo、Stepfun，以及生图、视频、语音、向量、3D、世界与决策模型。来源地址随每条目录记录保留，网络不可用时也可浏览。

1. 在右上角设置中选择百炼服务商，点击识别模型或回复模型旁的“浏览 / 搜索”，按名称、版本、类别筛选。
2. 截图模型只显示适用于当前图像识别流程的条目，回复模型只显示文字回复条目。点击“查看全部类别”或“百炼全部模型目录”可浏览全部 518 项；生图、语音、视频、向量等专用接口模型不会被误选为微信回复模型。
3. 选择识别模型后会自动带入适用的 JSON 输出格式；固定思考模型不再被强制关闭思考。保留手工填写新模型 ID 的能力。
4. 填写当前地域的百炼 Key 和接入地址后，可在目录窗口点击“同步官方模型目录”，通过官方 `GET /api/v1/models` 逐页获取全部结果，并使用模态和能力信息筛选任务。该操作只查询目录，不上传微信截图、不发起模型推理。
5. 同步结果按接入地址缓存至设置文件同目录的 `aliyun-models-*.json`，不包含密钥。目录返回不完整、重复分页、同步期间数量变化、请求失败或取消时保留原目录；完整同步后才替换缓存。

内置目录不代表每条模型在当前地域可用、已经为账户开通或具有免费额度。历史模型也可能已下线，以控制台和实际接口为准。北京、美国等地域若共享域名无法同步，请将 Base URL 改为控制台提供的业务空间专属 `https://{真实业务空间ID}.{地域}.maas.aliyuncs.com/compatible-mode/v1`，不要保留占位符；同步只使用该地址的同一主机，不转发 Key 到其他主机。参考：[官方模型大全](https://help.aliyun.com/zh/model-studio/models)、[官方目录接口与分页规则](https://help.aliyun.com/zh/model-studio/list-models)。

维护内置快照：运行 `python scripts/collect_model_docs.py` 获取公开官方文档到被 Git 忽略的 `.local/catalog-sources`，审阅后运行 `python scripts/build_model_catalog.py` 生成随程序打包的 `catalog_data.py`。只提取模型 ID 等事实元数据，不打包文档正文。

### 使用百炼免费额度

选择“阿里云百炼（北京）”，在百炼控制台获取该地域 API Key，确认两个模型有可用额度，再保存。官方当前规则中，中国站新人免费额度适用于北京地域，需完成实名认证，并有有效期；不同模型的额度不共享。是否领取、剩余数量和到期时间请以自己的控制台为准，软件不承诺免费或无限调用。建议在模型页面开启“免费额度用完即停”，避免用完后转为按量计费。

参考：[接入地址](https://help.aliyun.com/zh/model-studio/base-url)、[视觉理解](https://help.aliyun.com/zh/model-studio/vision)、[结构化输出](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output)、[新人免费额度规则](https://help.aliyun.com/zh/model-studio/new-free-quota)。

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
- 截图包含整个微信窗口，将发送至设置中选定的模型服务。云端使用 HTTPS；只有本机自定义服务允许 HTTP。OpenAI Responses 请求设置 `store=false`，并不代表服务端零保留；其他服务以其数据政策为准。密钥与聊天记录仅存内存；macOS 截图短暂写入临时目录，读取后立即删除，异常进程终止可能留下临时文件。
- 视觉结果可能出错。当前版本依赖标题、消息内容与置信度复核，没有可靠消息 ID；同一会话再次出现完全相同文字会被保守跳过。重启清空去重记录。
- 微信必须可见、无遮挡。窗口移动、最小化、前台焦点变化、低置信度或消息改变会停止操作。窗口坐标校验无法保证识别所有遮挡，使用时应保持微信完整可见。
- 人工模式需确认输入框为空，两种模式均通过截图复核空输入框及输入后的草稿，但视觉复核仍可能出错。不能自动证明点击后送达，界面明确记录“已点击发送”；不要在状态不明时重复发送。
- API 请求最长等待 90 秒；停止不打断网络请求，但请求返回后不继续发送。连续截图识别会产生 API 费用。

## 验证

```powershell
python -m unittest -v
python -m compileall -q app.py core.py settings.py settings_ui.py model_catalog.py catalog_data.py catalog_ui.py api_errors.py automation.py desktop.py desktop_windows.py desktop_macos.py
```

测试覆盖后台微信绑定、主窗口选择、Retina 坐标、自动切换会话和回复、草稿保护、消息变化、取消及去重，以及各服务商请求格式、模型拆分、配置保存、密钥隔离和错误分类。界面启动检查也会打开设置窗口、切换百炼预设并检查保存按钮布局，不发起真实 API 请求。真实微信识别、中文输入与发送需要在目标机器配置 API Key 后验收。

## CI 与发布

推送 main 或提交 PR 自动执行 Windows、macOS Apple Silicon / Intel 三平台测试、打包和成品启动检查。macOS 使用 python.org 官方 universal2 Python，部署目标为 13.0，并检查打包后二进制的最低系统版本。该静态检查不能替代 macOS 13 真机验收。

推送 `v0.1.0` 格式标签后，三个平台全部成功才创建 GitHub Release，上传 Windows 安装程序、两种 DMG 及 SHA256SUMS.txt。Release 使用 Actions 自带 GITHUB_TOKEN，不需要把个人令牌或 OpenAI Key 放入仓库 Secrets。

本地打包：安装 `requirements-build.txt` 后运行 `python scripts/build.py --version 0.4.0 --arch x86_64`。Windows 需 Inno Setup 6；macOS Apple Silicon 使用 `--arch arm64`。各平台须在对应系统构建。

官方资料：[Terra 模型](https://developers.openai.com/api/docs/models/gpt-6.1-terra)、[图像输入](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses)。
