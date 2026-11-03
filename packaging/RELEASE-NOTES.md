Windows 与 macOS 桌面原型：截图识别当前微信会话，生成可编辑回复，经逐条审核后逐字输入并点击发送。

## 安装包

- `windows-x64-setup.exe`：Windows 10/11，64 位安装程序。
- `macos-13-arm64.dmg`：macOS 13+，Apple Silicon。
- `macos-13-x86_64.dmg`：macOS 13+，Intel。
- `SHA256SUMS.txt`：文件校验值。

macOS：打开 DMG 后将应用拖入 Applications。需要授予“辅助功能”和“屏幕录制”权限，授权后重启应用。当前 macOS 应用仅 ad-hoc 签名，未经过 Apple Developer ID 签名或公证；Windows 安装包未签名。系统可能显示来源提示。

CI 执行单元测试、界面初始化、安装包构建和打包后启动检查；macOS 额外检查应用内 Mach-O 二进制最低系统版本不高于 13.0。尚未完成 macOS 13 真机及真实微信端到端验收。

需要自行提供 OpenAI API Key。截图会发往 OpenAI。当前版本每条回复须人工审核，不含无人值守自动发送或规避平台检测功能。
