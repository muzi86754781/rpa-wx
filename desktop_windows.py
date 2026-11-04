"""Windows Unicode SendInput, bounded smooth movement and cancellation."""
import ctypes as C
from ctypes import wintypes as W
import time
from PIL import ImageGrab
from typing_pace import type_deliberately

u = C.WinDLL('user32', use_last_error=True)
u.SetProcessDPIAware()
u.GetForegroundWindow.restype = W.HWND
u.GetWindowTextLengthW.argtypes = [W.HWND]
u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
u.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
u.IsWindow.argtypes = [W.HWND]
u.IsWindowVisible.argtypes = [W.HWND]
u.SetForegroundWindow.argtypes = [W.HWND]
u.IsIconic.argtypes = [W.HWND]
u.GetAncestor.argtypes = [W.HWND, W.UINT]
u.GetAncestor.restype = W.HWND
u.WindowFromPoint.argtypes = [W.POINT]
u.WindowFromPoint.restype = W.HWND


def permissions():
    return True


def foreground():
    return u.GetForegroundWindow()


def activate(hwnd):
    return u.SetForegroundWindow(hwnd)


def is_wechat(hwnd):
    return any(name in title(hwnd).lower() for name in ('微信', 'wechat', 'weixin'))


def find_wechat():
    candidates = []
    callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    u.EnumWindows.argtypes = [callback_type, W.LPARAM]
    def visit(hwnd, unused):
        try:
            if u.IsWindowVisible(hwnd) and is_wechat(hwnd) and not u.IsIconic(hwnd):
                l, t, r, b = rect(hwnd)
                if r - l > 300 and b - t > 300:
                    candidates.append(((r - l) * (b - t), hwnd))
        except RuntimeError:
            pass  # A window can close while EnumWindows is running.
        return True
    u.EnumWindows(callback_type(visit), 0)
    if not candidates:
        raise RuntimeError('未找到微信主窗口。请先打开、登录微信并取消最小化。')
    return max(candidates)[1]


def capture(hwnd):
    return ImageGrab.grab(bbox=rect(hwnd), all_screens=True)


class KEYBDINPUT(C.Structure):
    _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD),
                ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]


class MOUSEINPUT(C.Structure):
    _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('mouseData', W.DWORD),
                ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]


class UNION(C.Union):
    _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', W.DWORD), ('data', UNION)]


def key(vk=0, scan=0, flags=0):
    event = INPUT(type=1, ki=KEYBDINPUT(vk, scan, flags, 0, 0))
    if u.SendInput(1, C.byref(event), C.sizeof(INPUT)) != 1:
        raise RuntimeError('Windows 拒绝输入，检查目标程序权限级别。')


def title(hwnd):
    buf = C.create_unicode_buffer(u.GetWindowTextLengthW(hwnd) + 1)
    u.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def rect(hwnd):
    r = W.RECT()
    if not u.IsWindow(hwnd) or u.IsIconic(hwnd) or not u.GetWindowRect(hwnd, C.byref(r)):
        raise RuntimeError('微信窗口已关闭或最小化。')
    return r.left, r.top, r.right, r.bottom


def cursor():
    p = W.POINT()
    u.GetCursorPos(C.byref(p))
    return p.x, p.y


def check(hwnd, stop):
    if stop.is_set() or u.GetAsyncKeyState(0x1B) & 0x8000:
        raise RuntimeError('操作已停止。输入框内可能保留部分草稿，请检查。')
    if u.GetForegroundWindow() != hwnd:
        raise RuntimeError('前台窗口发生变化，已停止。')


def move(hwnd, point, stop):
    start = cursor()
    for i in range(1, 31):
        check(hwnd, stop)
        t = i / 30
        ease = t * t * (3 - 2 * t)
        u.SetCursorPos(round(start[0] + (point[0] - start[0]) * ease),
                       round(start[1] + (point[1] - start[1]) * ease))
        time.sleep(.012)
    if u.GetAncestor(u.WindowFromPoint(W.POINT(*point)), 2) != hwnd:
        raise RuntimeError('目标位置被其他窗口遮挡。')
    check(hwnd, stop)


def click(hwnd, point, stop):
    move(hwnd, point, stop)
    u.mouse_event(2, 0, 0, 0, 0)
    u.mouse_event(4, 0, 0, 0, 0)


def type_text(hwnd, text, stop, delay=None):
    def emit(char):
        check(hwnd, stop)
        if char == '\n':
            key(0x10)
            try:
                key(0x0D)
                key(0x0D, flags=2)
            finally:
                key(0x10, flags=2)
        else:
            raw = char.encode('utf-16-le')
            for i in range(0, len(raw), 2):
                unit = int.from_bytes(raw[i:i + 2], 'little')
                key(scan=unit, flags=4)
                key(scan=unit, flags=6)
    def backspace():
        check(hwnd, stop)
        key(0x08)
        key(0x08, flags=2)
    type_deliberately(text, stop, lambda: check(hwnd, stop), emit, backspace, delay=delay)


def scroll(hwnd, point, lines, stop):
    move(hwnd, point, stop)
    check(hwnd, stop)
    u.mouse_event(0x0800, 0, 0, lines * 120, 0)
