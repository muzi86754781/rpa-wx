"""macOS 13+ desktop backend; CoreGraphics coordinates use logical points."""
import os
import subprocess
import tempfile
import time

import AppKit
import ApplicationServices as AX
import Quartz as Q
from PIL import Image

WECHAT_BUNDLES = {'com.tencent.xinwechat', 'com.tencent.wechat',
                  'com.tencent.xinweixin', 'com.tencent.weixin'}


def wechat_app(app):
    if app is None:
        return False
    return str(app.bundleIdentifier() or '').lower() in WECHAT_BUNDLES


def main_window(pid):
    candidates = []
    for item in windows():
        bounds = item.get(Q.kCGWindowBounds, {})
        width, height = bounds.get('Width', 0), bounds.get('Height', 0)
        if (item.get(Q.kCGWindowOwnerPID) == pid and item.get(Q.kCGWindowLayer) == 0
                and width > 300 and height > 300):
            candidates.append((width * height, int(item[Q.kCGWindowNumber])))
    return max(candidates)[1] if candidates else None


def find_wechat():
    """Discover the actual process, even while our own UI has focus."""
    apps = AppKit.NSWorkspace.sharedWorkspace().runningApplications()
    for app in apps:
        if wechat_app(app):
            app.unhide()
            app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
            # Activation can restore a hidden window asynchronously.
            for _ in range(20):
                identifier = main_window(app.processIdentifier())
                if identifier:
                    return identifier
                time.sleep(.1)
            raise RuntimeError('微信正在运行，但未找到主窗口。请打开微信聊天列表，退出最小化或全屏后重试。')
    raise RuntimeError('未找到桌面微信进程。请先打开并登录微信（WeChat / Weixin）。')


def permissions():
    trusted = AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True})
    recording = Q.CGPreflightScreenCaptureAccess()
    if not recording:
        Q.CGRequestScreenCaptureAccess()
    if not trusted or not recording:
        raise RuntimeError('请在系统设置 → 隐私与安全性中，为 WeChat Agent 开启辅助功能和屏幕录制，然后重启应用。')
    return True


def windows():
    return Q.CGWindowListCopyWindowInfo(
        Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements,
        Q.kCGNullWindowID) or []


def window(identifier):
    for item in windows():
        if int(item[Q.kCGWindowNumber]) == identifier:
            return item
    raise RuntimeError('绑定窗口已关闭、最小化或移出当前桌面。')


def foreground():
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    return main_window(app.processIdentifier())


def application(identifier):
    pid = window(identifier)[Q.kCGWindowOwnerPID]
    return AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)


def activate(identifier):
    app = application(identifier)
    if not app:
        raise RuntimeError('微信进程已退出。')
    app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)


def title(identifier):
    item = window(identifier)
    return str(item.get(Q.kCGWindowName) or item.get(Q.kCGWindowOwnerName) or '')


def is_wechat(identifier):
    return wechat_app(application(identifier))


def rect(identifier):
    b = window(identifier)[Q.kCGWindowBounds]
    x, y, w, h = (round(b[key]) for key in ('X', 'Y', 'Width', 'Height'))
    return x, y, x + w, y + h


def cursor():
    point = Q.CGEventGetLocation(Q.CGEventCreate(None))
    return round(point.x), round(point.y)


def check(identifier, stop):
    if stop.is_set() or Q.CGEventSourceKeyState(Q.kCGEventSourceStateCombinedSessionState, 53):
        raise RuntimeError('操作已停止。微信可能保留部分输入，请检查。')
    if foreground() != identifier:
        raise RuntimeError('前台窗口发生变化，已停止。')


def capture(identifier):
    # screencapture handles Retina backing resolution and secondary displays.
    # No shell interpolation; the temporary screenshot is removed immediately.
    with tempfile.TemporaryDirectory(prefix='wechat-agent-') as folder:
        path = os.path.join(folder, 'window.png')
        result = subprocess.run(['/usr/sbin/screencapture', '-x', '-o', '-l',
                                 str(identifier), path], capture_output=True, timeout=15)
        if result.returncode or not os.path.exists(path):
            raise RuntimeError('无法截取微信窗口，请检查屏幕录制权限。')
        with Image.open(path) as image:
            return image.convert('RGB')


def move(identifier, point, stop):
    start = cursor()
    for i in range(1, 31):
        check(identifier, stop)
        t = i / 30
        ease = t * t * (3 - 2 * t)
        position = (start[0] + (point[0] - start[0]) * ease,
                    start[1] + (point[1] - start[1]) * ease)
        event = Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, position, Q.kCGMouseButtonLeft)
        Q.CGEventPost(Q.kCGHIDEventTap, event)
        time.sleep(.012)
    # Window list is front to back. Do not click a dialog or overlay.
    for item in windows():
        b = item.get(Q.kCGWindowBounds, {})
        if (b.get('X', 0) <= point[0] < b.get('X', 0) + b.get('Width', 0)
                and b.get('Y', 0) <= point[1] < b.get('Y', 0) + b.get('Height', 0)):
            if int(item[Q.kCGWindowNumber]) != identifier:
                raise RuntimeError('目标位置被其他窗口遮挡。')
            break
    check(identifier, stop)


def click(identifier, point, stop):
    move(identifier, point, stop)
    for kind in (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp):
        Q.CGEventPost(Q.kCGHIDEventTap, Q.CGEventCreateMouseEvent(None, kind, point, Q.kCGMouseButtonLeft))


def type_text(identifier, text, stop, delay=.065):
    for char in text:
        check(identifier, stop)
        for down in (True, False):
            event = Q.CGEventCreateKeyboardEvent(None, 36 if char == '\n' else 0, down)
            if char == '\n':
                Q.CGEventSetFlags(event, Q.kCGEventFlagMaskShift)
            else:
                units = len(char.encode('utf-16-le')) // 2
                Q.CGEventKeyboardSetUnicodeString(event, units, char)
            Q.CGEventPost(Q.kCGHIDEventTap, event)
        time.sleep(delay)


def scroll(identifier, point, lines, stop):
    # Move and check occlusion without selecting a different conversation.
    move(identifier, point, stop)
    check(identifier, stop)
    event = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 1, lines)
    Q.CGEventPost(Q.kCGHIDEventTap, event)
