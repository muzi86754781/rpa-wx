"""Windows Unicode SendInput, bounded scrolling and pointer takeover."""
import ctypes as C
from ctypes import wintypes as W
from threading import RLock
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
u.GetCursorPos.argtypes = [C.POINTER(W.POINT)]
u.SetCursorPos.argtypes = [C.c_int, C.c_int]
u.GetAsyncKeyState.argtypes = [C.c_int]
u.GetAsyncKeyState.restype = C.c_short
u.SystemParametersInfoW.argtypes = [W.UINT, W.UINT, C.c_void_p, W.UINT]

_input_lock = RLock()
_input_watch = None
WHEEL_STEP = 120


def permissions():
    return True


def work_area():
    bounds = W.RECT()
    if u.SystemParametersInfoW(0x0030, 0, C.byref(bounds), 0):
        return bounds.left, bounds.top, bounds.right, bounds.bottom
    return None


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


u.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
u.SendInput.restype = W.UINT


def send_input(*events):
    batch = (INPUT * len(events))(*events)
    if u.SendInput(len(batch), batch, C.sizeof(INPUT)) != len(batch):
        raise RuntimeError('Windows 拒绝输入，检查目标程序权限级别。')


def key(vk=0, scan=0, flags=0):
    send_input(INPUT(type=1, ki=KEYBDINPUT(vk, scan, flags, 0, 0)))


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


class InputWatch:
    def __init__(self, stop):
        self.stop = stop
        self.position = cursor()
        self.reason = None

    def poll(self):
        if not self.stop.is_set():
            x, y = cursor()
            if escape_pressed():
                self.reason = '已按 Esc 停止。'
            elif max(abs(x - self.position[0]), abs(y - self.position[1])) > 4:
                self.reason = '检测到鼠标移动，已停止自动操作。'
            if self.reason:
                self.stop.set()
        return self.reason


def escape_pressed():
    return bool(u.GetAsyncKeyState(0x1B) & 0x8000)


def start_input_watch(stop):
    global _input_watch
    with _input_lock:
        _input_watch = InputWatch(stop)


def poll_input_watch(stop):
    with _input_lock:
        if _input_watch and _input_watch.stop is stop:
            return _input_watch.poll()
    return None


def end_input_watch(stop):
    global _input_watch
    with _input_lock:
        if _input_watch and _input_watch.stop is stop:
            _input_watch = None


def check(hwnd, stop):
    reason = poll_input_watch(stop)
    if reason:
        raise RuntimeError(reason)
    if stop.is_set() or escape_pressed():
        raise RuntimeError('操作已停止。输入框内可能保留部分草稿，请检查。')
    if u.GetForegroundWindow() != hwnd:
        raise RuntimeError('前台窗口发生变化，已停止。')


def move(hwnd, point, stop):
    check(hwnd, stop)
    if u.GetAncestor(u.WindowFromPoint(W.POINT(*point)), 2) != hwnd:
        raise RuntimeError('目标位置被其他窗口遮挡。')
    with _input_lock:
        check(hwnd, stop)
        if cursor() != point and not u.SetCursorPos(*point):
            raise RuntimeError('Windows 无法将鼠标移到目标位置。')
        if _input_watch and _input_watch.stop is stop:
            _input_watch.position = point


def mouse_input(hwnd, point, stop, *events):
    move(hwnd, point, stop)
    check(hwnd, stop)
    if cursor() != point:
        raise RuntimeError('鼠标已离开目标位置，操作停止。')
    send_input(*events)


def click(hwnd, point, stop):
    mouse_input(hwnd, point, stop, INPUT(type=0, mi=MOUSEINPUT(dwFlags=2)),
                INPUT(type=0, mi=MOUSEINPUT(dwFlags=4)))


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
    steps = max(-4, min(4, lines))
    mouse_input(hwnd, point, stop,
                INPUT(type=0, mi=MOUSEINPUT(mouseData=steps * WHEEL_STEP, dwFlags=0x0800)))
