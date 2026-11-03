# WeChat Agent（Windows / macOS 原型）

参考图左侧面板的本地桌面助手：微信窗口截图 → GPT-6.1 Terra 读取会话 → 大模型生成回复 → 人工编辑审核 → Windows 鼠标定位、Unicode 逐字输入、点击发送。

## 运行

安装包见 [GitHub Releases](https://github.com/muzi86754781/rpa-wx/releases/latest)：Windows 10/11 x64 安装程序、macOS 13+ Apple Silicon 和 Intel DMG。安装包不需要单独安装 Python。macOS 请先把应用拖入 Applications，并在系统设置中授予“辅助功能”和“屏幕录制”权限，授权后重启。当前无开发者证书签名/公证，详情见 DMG 内说明。

从源代码运行需要 Windows 10/11 或 macOS 13+、Python 3.11+ 和已登录的桌面微信。macOS 推荐 python.org 官方 Python（包含 Tcl/Tk），命令使用 `python3`。

```powershell
python -m pip install -r requirements.txt
python app.py
```

也可双击 `start.cmd`。API Key 在界面填写，或通过 `OPENAI_API_KEY` 环境变量提供；不会写入磁盘。默认模型为 `gpt-6.1-terra`，账户必须有对应 API 权限与余额。

## 使用

1. 点击“绑定窗口”，在四秒内切到微信。窗口标题须含“微信”、WeChat 或 Weixin。
2. 点击“校准输入框”，倒计时内将鼠标停在输入框内；同样校准“发送按钮”。此后不要移动或缩放微信窗口。
3. 打开目标聊天，点击“识别最新消息”。仅识别当前可见会话，不遍历联系人、不读取后台数据库。
4. 审核、编辑草稿。确认微信输入框为空，且微信设置使用 Enter 发送、Shift+Enter 换行，再勾选确认。
5. 点击“审核通过”。程序切回微信，再次识别标题与最新消息，逐字输入，重新核对会话后点击发送。输入期间不要操作电脑。
6. 停止按钮取消后续动作；输入过程中按住 Esc 也可中止。部分输入可能留在微信输入框，请手动处理。

监控开关每十秒检查一次，仅微信在前台时生效。已有草稿时暂停识别，发送仍须逐条审核。清空编辑区后再次点识别可释放旧草稿，再点一次开始重新识别。

## 边界与实现

- 这是人工审核的可运行原型，未实现无人值守自动发送。平滑鼠标使用加减速插值，输入按 Unicode 字符逐个注入；没有规避检测、随机错误、假装真人或反风控功能，也无法承诺微信不会识别自动化。
- 截图包含整个微信窗口，将通过 HTTPS 发给 OpenAI。接口设置 `store=false`，并不代表服务端零保留；请自行选择适合上传的会话。密钥与聊天记录仅存内存；macOS 截图短暂写入临时目录，读取后立即删除，异常进程终止可能留下临时文件。
- 视觉结果可能出错。当前版本依赖标题、消息内容与置信度复核，没有可靠消息 ID；同一会话再次出现完全相同文字会被保守跳过。重启清空去重记录。
- 微信必须可见、无遮挡。窗口移动、最小化、前台焦点变化、低置信度或消息改变会停止操作。窗口坐标校验无法保证识别所有遮挡，使用时应保持微信完整可见。
- 用户需确认输入框为空。不能自动证明点击后送达，界面明确记录“已点击发送”；不要在状态不明时重复发送。
- API 请求最长等待 90 秒；停止不打断网络请求，但请求返回后不继续发送。连续截图识别会产生 API 费用。

## 验证

```powershell
python -m unittest -v
python -m compileall -q app.py core.py desktop.py
```

测试覆盖响应提取、拒绝结果、会话去重和无效识别数据。真实微信识别、中文输入与发送需要在目标机器配置 API Key 后验收。

## CI 与发布

推送 main 或提交 PR 自动执行 Windows、macOS Apple Silicon / Intel 三平台测试、打包和成品启动检查。macOS 使用 python.org 官方 universal2 Python，部署目标为 13.0，并检查打包后二进制的最低系统版本。该静态检查不能替代 macOS 13 真机验收。

推送 `v0.1.0` 格式标签后，三个平台全部成功才创建 GitHub Release，上传 Windows 安装程序、两种 DMG 及 SHA256SUMS.txt。Release 使用 Actions 自带 GITHUB_TOKEN，不需要把个人令牌或 OpenAI Key 放入仓库 Secrets。

本地打包：安装 `requirements-build.txt` 后运行 `python scripts/build.py --version 0.1.0 --arch x86_64`。Windows 需 Inno Setup 6；macOS Apple Silicon 使用 `--arch arm64`。各平台须在对应系统构建。

官方资料：[Terra 模型](https://developers.openai.com/api/docs/models/gpt-6.1-terra)、[图像输入](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses)。
