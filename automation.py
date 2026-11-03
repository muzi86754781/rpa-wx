"""Cancellable conversation switching and replies, independent of the Tk UI."""
import hashlib

from core import fingerprint


def replyable(view):
    return (view['incoming'] and view['confidence'] >= .9
            and all(fingerprint(view)))


class ConversationSession:
    def __init__(self, desktop, hwnd, bounds, points, cancel, seen):
        self.desktop, self.hwnd, self.bounds = desktop, hwnd, bounds
        self.points, self.cancel, self.seen = dict(points), cancel, seen
        self.pages = set()
        self.cached_digest = None
        self.cached_view = None

    def validate(self):
        if self.desktop.rect(self.hwnd) != self.bounds:
            raise RuntimeError('微信窗口位置或尺寸改变，请重新绑定和校准。')
        self.desktop.check(self.hwnd, self.cancel)

    def wait(self, seconds):
        if self.cancel.wait(seconds):
            raise RuntimeError('操作已停止。请检查微信输入框。')
        self.validate()

    def focus(self):
        if self.cancel.is_set():
            raise RuntimeError('操作已停止。')
        if self.desktop.rect(self.hwnd) != self.bounds:
            raise RuntimeError('微信窗口位置或尺寸改变，请重新绑定和校准。')
        self.desktop.activate(self.hwnd)
        self.wait(.5)

    def capture(self):
        self.validate()
        return self.desktop.capture(self.hwnd)

    def inspect(self, client):
        image = self.capture()
        view = client.inspect(image)
        self.validate()  # A network call may outlive Stop or a focus change.
        return view

    def point(self, x, y):
        l, t, r, b = self.bounds
        return round(l + (r - l) * x / 1000), round(t + (b - t) * y / 1000)

    def send(self, client, view, text):
        text = text.strip()
        if not text or len(text) > 2000:
            raise ValueError('回复为空或超过 2000 字，已停止。')
        if set(self.points) != {'input', 'send'}:
            raise ValueError('请先校准输入框和发送按钮。')
        self.focus()
        fresh = self.inspect(client)
        if fingerprint(fresh) != fingerprint(view) or not replyable(fresh):
            raise RuntimeError('会话或最新消息已变化，已停止发送。')
        if not fresh['input_empty']:
            raise RuntimeError('微信输入框已有内容或无法确认其为空，已停止，请手动处理。')
        self.desktop.click(self.hwnd, self.points['input'], self.cancel)
        self.desktop.type_text(self.hwnd, text, self.cancel)
        fresh = self.inspect(client)
        if (fingerprint(fresh) != fingerprint(view) or not replyable(fresh)
                or fresh['input_text'].strip().replace('\r\n', '\n') != text.replace('\r\n', '\n')):
            raise RuntimeError('输入期间会话、消息或草稿变化，已保留微信草稿，请手动检查。')
        self.validate()
        self.desktop.click(self.hwnd, self.points['send'], self.cancel)
        # Mark immediately after clicking; the UI event queue must not control dedupe.
        self.seen.add(fingerprint(view))
        self.cached_digest = None

    def poll(self, client, persona):
        """Handle one incoming message or advance through the conversation list."""
        self.focus()
        image = self.capture()
        digest = hashlib.sha256(image.tobytes()).digest()
        if digest == self.cached_digest:
            view = self.cached_view
        else:
            view = client.inspect(image)
            self.validate()
            self.cached_digest, self.cached_view = digest, view
        if not view['input_empty']:
            raise RuntimeError('当前微信输入框已有草稿或看不清，自动回复已停止，请手动处理。')
        if replyable(view) and fingerprint(view) not in self.seen:
            text = client.reply(view, persona)
            self.validate()
            self.send(client, view, text)
            return 'sent', (view, text)

        for row in view['unread']:
            if row['confidence'] < .95 or row['conversation'].strip() == view['conversation'].strip():
                continue
            # Re-read immediately before using coordinates from a prior API call.
            current = self.inspect(client)
            if not current['input_empty']:
                raise RuntimeError('切换会话前发现微信草稿，自动回复已停止。')
            target = next((item for item in current['unread']
                           if item['conversation'] == row['conversation'] and item['confidence'] >= .95), None)
            if target is None:
                return 'status', '未读列表发生变化，下一轮重新检查。'
            self.desktop.click(self.hwnd, self.point(target['x'], target['y']), self.cancel)
            self.wait(.6)
            selected = self.inspect(client)
            if selected['conversation'].strip() != target['conversation'].strip():
                raise RuntimeError('打开的会话与未读联系人名称不一致，自动回复已停止。')
            if not selected['input_empty']:
                raise RuntimeError('目标会话已有草稿，自动回复已停止，请手动处理。')
            if not replyable(selected):
                raise RuntimeError('已打开未读会话，但无法确认最新消息，自动回复已停止。')
            if fingerprint(selected) in self.seen:
                return 'status', '该会话消息已处理，跳过重复回复。'
            text = client.reply(selected, persona)
            self.validate()
            self.send(client, selected, text)
            return 'sent', (selected, text)

        box = view['list_box']
        if box:
            # Sweep beyond the visible rows; repeated pages indicate the bottom.
            crop = image.crop((image.width * box['left'] // 1000,
                               image.height * box['top'] // 1000,
                               image.width * box['right'] // 1000,
                               image.height * box['bottom'] // 1000))
            page = hashlib.sha256(crop.tobytes()).digest()
            end = page in self.pages or len(self.pages) >= 50
            if end:
                self.pages.clear()
            else:
                self.pages.add(page)
            self.validate()
            self.desktop.scroll(self.hwnd, self.point((box['left'] + box['right']) / 2,
                                (box['top'] + box['bottom']) / 2), 1000 if end else -8, self.cancel)
            self.wait(.3)
        return 'status', '自动回复运行中，正在检查当前聊天和未读会话列表…'
