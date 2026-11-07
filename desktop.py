"""Platform facade shared by the desktop UI."""
import sys

visual_badges = sys.platform == 'win32'

if sys.platform == 'win32':
    from desktop_windows import (foreground, activate, title, rect, cursor, check,
                                 click, type_text, capture, is_wechat, permissions,
                                 find_wechat, scroll, work_area, escape_pressed,
                                 start_input_watch, poll_input_watch, end_input_watch)
elif sys.platform == 'darwin':
    from desktop_macos import (foreground, activate, title, rect, cursor, check,
                               click, type_text, capture, is_wechat, permissions,
                               find_wechat, scroll)
else:
    raise RuntimeError('WeChat Agent supports Windows and macOS only.')
