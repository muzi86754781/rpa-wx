"""Platform facade shared by the desktop UI."""
import sys

if sys.platform == 'win32':
    from desktop_windows import (foreground, activate, title, rect, cursor, check,
                                 click, type_text, capture, is_wechat, permissions)
elif sys.platform == 'darwin':
    from desktop_macos import (foreground, activate, title, rect, cursor, check,
                               click, type_text, capture, is_wechat, permissions)
else:
    raise RuntimeError('WeChat Agent supports Windows and macOS only.')
