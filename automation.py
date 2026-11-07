"""Cancellable conversation switching and replies, independent of the Tk UI."""
import hashlib
import time
from dataclasses import dataclass

from core import fingerprint, numbered_badge
from api_errors import APIError
from scrolling import content_direction
from diagnostics import record
from badges import numbered_candidates, OpenedRow


def replyable(view):
    return (view['incoming'] and view['confidence'] >= .9
            and all(fingerprint(view)))


class SkipConversation(RuntimeError):
    """A pre-input problem in one conversation need not stop the whole sweep."""


@dataclass
class SweepStats:
    checked: int = 0
    opened: int = 0
    sent: int = 0
    skipped: int = 0

    def summary(self):
        return (f'已检查列表 {self.checked} 屏次，打开 {self.opened} 个未读会话，'
                f'已点击发送 {self.sent} 条，跳过 {self.skipped} 项。')


class ConversationSession:
    def __init__(self, desktop, hwnd, bounds, points, cancel, seen):
        self.desktop, self.hwnd, self.bounds = desktop, hwnd, bounds
        self.points, self.cancel, self.seen = dict(points), cancel, seen
        self.cached_digest = None
        self.cached_view = None
        self.input_started = False
        self.list_box = None
        self.bottom_still = 0
        self.skipped = {}
        self.stats = SweepStats()
        self.sweeping = False
        self.report = lambda kind, value: None
        self.opened_pending = None
        self.opened_row = None
        self.failed_badges = set()
        self.current_states = {}
        self.pending_current = set()
        self.scroll_sign = 1
        self.scroll_direction_known = False
        self.scroll_boundary_verified = False
        self.scrolled_down = False
        self.focused = False
        self.need_full_view = True
        self.fast_box = None
        self.cached_scope = None

    def prepare_retry(self):
        if self.input_started:
            return False
        # A delayed retry must re-read the desktop, never reuse old coordinates.
        self.cached_digest = None
        self.bottom_still = 0
        return True

    def validate(self):
        if self.desktop.rect(self.hwnd) != self.bounds:
            raise RuntimeError('微信窗口位置或尺寸改变，请重新绑定和校准。')
        self.desktop.check(self.hwnd, self.cancel)

    def wait(self, seconds):
        if self.cancel.wait(seconds):
            raise RuntimeError('操作已停止。请检查微信输入框。')
        self.validate()

    def focus(self):
        if self.focused:
            self.validate()
            return
        if self.cancel.is_set():
            raise RuntimeError('操作已停止。')
        if self.desktop.rect(self.hwnd) != self.bounds:
            raise RuntimeError('微信窗口位置或尺寸改变，请重新绑定和校准。')
        self.desktop.activate(self.hwnd)
        self.wait(.5)
        self.focused = True

    def capture(self):
        self.validate()
        return self.desktop.capture(self.hwnd)

    def inspect(self, client):
        image = self.capture()
        self.check_opened_row(image)
        view = client.inspect(image)
        self.validate()  # A network call may outlive Stop or a focus change.
        if self.opened_row:
            self.check_opened_row(self.capture())
        self.observe_current(view)
        return view

    def check_opened_row(self, image):
        if self.opened_row and not self.opened_row.matches(image):
            if self.input_started:
                raise RuntimeError('已选中的未读会话位置或选中状态变化，草稿未发送。')
            raise SkipConversation('已选中的未读会话位置或选中状态变化，未输入。')

    def clear_opened(self):
        self.opened_pending = None
        self.opened_row = None

    def observe_current(self, view):
        name = view['conversation'].strip()
        if not name:
            return
        state = (view['incoming'], view['message'].strip())
        previous = self.current_states.get(name)
        if previous is not None and previous != state and replyable(view):
            self.pending_current.add(fingerprint(view))
        self.current_states[name] = state

    def read_page(self, client):
        previous_box = self.fast_box
        supports_list = callable(getattr(type(client), 'inspect_list', None))
        for attempt in range(2):
            image = self.capture()
            use_list = bool(supports_list and previous_box and not self.opened_pending
                            and (attempt or not self.need_full_view))
            scope = 'list' if use_list else 'full'
            digest = self.list_digest(image, previous_box) if use_list else hashlib.sha256(image.tobytes()).digest()
            if attempt == 0 and scope == self.cached_scope and digest == self.cached_digest:
                view = self.cached_view
                record('list_cache', cache_hit=True, stage=scope)
                break
            view = client.inspect_list(image, previous_box) if use_list else client.inspect(image)
            self.validate()
            if view['list_box'] and not use_list:
                previous_box = dict(view['list_box'])
                self.fast_box = previous_box
            needs_review = (not view['list_box'] or view.get('navigation_error')
                            or view.get('navigation_issues'))
            if not needs_review or attempt == 1:
                break
            self.report('status', '未读列表坐标识别不一致，正在重新截图复核；暂不点击…')
            self.wait(.2)
        if not view['list_box']:
            raise RuntimeError('重新识别后仍无法定位左侧会话列表，未点击。请完整显示微信主窗口和左侧列表，'
                               '或在设置中更换截图识别模型后重试。')
        for name, reason in view.get('navigation_issues', []):
            self.skip(name, reason)
        self.cached_digest, self.cached_view = digest, view
        self.cached_scope = scope
        self.need_full_view = False
        self.list_box = view['list_box']
        self.observe_current(view)
        self.stats.checked += 1
        return image, view

    def list_digest(self, image, box):
        crop = image.crop((image.width * box['left'] // 1000,
                           image.height * box['top'] // 1000,
                           image.width * box['right'] // 1000,
                           image.height * box['bottom'] // 1000))
        return hashlib.sha256(crop.tobytes()).digest()

    def emit_scroll(self, box, lines):
        self.validate()
        self.desktop.scroll(self.hwnd, self.point((box['left'] + box['right']) / 2,
                            box['top'] + (box['bottom'] - box['top']) * .8),
                            lines * self.scroll_sign, self.cancel)
        self.wait(.5)
        return self.capture()

    def scroll_digest(self, image, box):
        # Transient scrollbar paint does not change which conversations are visible.
        content_box = dict(box, right=box['right'] - (box['right'] - box['left']) // 12)
        return self.list_digest(image, content_box)

    def scroll_list(self, image, box, lines, *, probe_boundary=True):
        self.scroll_boundary_verified = False
        before = self.scroll_digest(image, box)
        after = self.emit_scroll(box, lines)
        moved = self.scroll_digest(after, box) != before
        record('scroll', lines=lines, moved=moved, scroll_sign=self.scroll_sign)
        expected = 1 if lines > 0 else -1
        # Large return-to-top jumps may have no overlapping rows. Only small
        # steps can establish direction; unrelated rows must not flip it.
        direction = content_direction(image, after, box) if moved and abs(lines) == 1 else 0
        if direction:
            self.scroll_direction_known = True
            if direction != expected:
                self.scroll_sign *= -1
                self.report('status', '检测到列表实际滚动方向相反，已自动校正，继续检查…')
                # Undo the wrong step, then take the requested step. Subsequent
                # rows are always re-read before using any coordinates.
                restored = self.emit_scroll(box, lines)
                after = self.emit_scroll(box, 1 if lines > 0 else -1)
                direction = content_direction(restored, after, box)
                if direction and direction != expected:
                    raise RuntimeError('列表滚动方向校正后仍不一致，本轮未完成；请检查滚动增强软件。')
            if lines < 0 and direction == expected:
                self.scrolled_down = True
            return after, True
        if moved:
            return after, True
        if not probe_boundary:
            self.scroll_boundary_verified = True
            return after, False
        # A no-op alone cannot distinguish an inverted direction at the top
        # from a real bottom. Probe one small step in the other direction.
        probe_lines = -1 if lines > 0 else 1
        probe = self.emit_scroll(box, probe_lines)
        if self.scroll_digest(probe, box) == before:
            return probe, False
        direction = content_direction(image, probe, box)
        if direction == expected:
            self.scroll_sign *= -1
            self.scroll_direction_known = True
            if lines < 0:
                self.scrolled_down = True
            self.report('status', '检测到自然滚动或反向滚动，已按实际移动方向校正…')
            return probe, True  # The probe already moved in the desired direction.
        if direction:
            self.scroll_direction_known = True
        restored = self.emit_scroll(box, -probe_lines)
        unchanged = self.scroll_digest(restored, box) == before
        self.scroll_boundary_verified = unchanged and self.scroll_direction_known
        return restored, not unchanged

    def go_to_top(self, image, box):
        still = 0
        self.scrolled_down = False
        for _ in range(160):
            known = self.scroll_direction_known
            image, moved = self.scroll_list(image, box, 4 if known else 1, probe_boundary=not known)
            still = 0 if moved else still + 1
            if still >= 2:
                self.bottom_still = 0
                self.cached_digest = None
                return
        raise RuntimeError('会话列表一直变化，尚未确认滚到顶部；本轮未完成，请稍后重试。')

    def skip(self, name, reason):
        name = name.strip() or '当前聊天（未识别标题）'
        if name not in self.skipped:
            self.skipped[name] = reason
            self.stats.skipped += 1
            self.report('skip', (name, reason))
            record('conversation_skipped', conversation=name, reason=reason)

    def point(self, x, y):
        l, t, r, b = self.bounds
        return round(l + (r - l) * x / 1000), round(t + (b - t) * y / 1000)

    def send(self, client, view, text):
        text = text.strip()
        if not text or len(text) > 2000:
            raise SkipConversation('回复为空或超过 2000 字，未输入。')
        if set(self.points) != {'input', 'send'}:
            raise ValueError('请先校准输入框和发送按钮。')
        self.focus()
        record('send_stage', conversation=view['conversation'], stage='before_input')
        fresh = self.inspect(client)
        if fingerprint(fresh) != fingerprint(view) or not replyable(fresh):
            raise SkipConversation('会话或最新消息已变化，未输入；请稍后重新打开该会话检查。')
        if not fresh['input_empty']:
            raise SkipConversation('微信输入框已有草稿或无法确认其为空，未改动草稿。')
        self.input_started = True
        record('send_stage', conversation=view['conversation'], stage='typing', characters=len(text))
        self.desktop.click(self.hwnd, self.points['input'], self.cancel)
        started = time.monotonic()
        self.desktop.type_text(self.hwnd, text, self.cancel)
        record('send_stage', conversation=view['conversation'], stage='verify_input', characters=len(text),
               duration_ms=round((time.monotonic() - started) * 1000))
        try:
            fresh = self.inspect(client)
        except APIError as error:
            error.input_pending = True
            raise
        if (fingerprint(fresh) != fingerprint(view) or not replyable(fresh)
                or fresh['input_text'].strip().replace('\r\n', '\n') != text.replace('\r\n', '\n')):
            raise RuntimeError('输入期间会话、消息或草稿变化，已保留微信草稿，请手动检查。')
        self.validate()
        self.desktop.click(self.hwnd, self.points['send'], self.cancel)
        record('send_stage', conversation=view['conversation'], stage='clicked_send', characters=len(text))
        # Mark immediately after clicking; the UI event queue must not control dedupe.
        self.seen.add(fingerprint(view))
        self.cached_digest = None
        self.input_started = False
        self.need_full_view = True

    def reply_current(self, client, view, persona):
        name = view['conversation'].strip()
        if name in self.skipped and not self.opened_row:
            return None
        if not view['input_empty']:
            self.skip(name, '输入框已有草稿或看不清，保留草稿并继续其他会话。')
            return None
        if not replyable(view):
            if view['incoming']:
                self.skip(name, '对方消息或会话标题不清晰，识别置信度不足。')
            return None
        if fingerprint(view) in self.seen and fingerprint(view) not in self.pending_current:
            return None
        text = client.reply(view, persona)
        self.validate()
        try:
            self.send(client, view, text)
        except SkipConversation as error:
            self.skip(name, str(error))
            return None
        self.stats.sent += 1
        self.pending_current.discard(fingerprint(view))
        self.clear_opened()
        return view, text

    def badge_at_row(self, row, image, candidates):
        y = row['y'] * image.height / 1000
        matches = [badge for badge in candidates if abs(badge.point[1] - y) <= badge.height * 2]
        return matches[0] if len(matches) == 1 else None

    def poll(self, client, persona, allow_scroll=True, unread_only=False):
        """One sweep step: current pending message, another unread, or next page."""
        self.focus()
        image, view = self.read_page(client)
        visual = getattr(self.desktop, 'visual_badges', False) is True
        current_name = view['conversation'].strip()
        if self.opened_row:
            if not self.opened_row.matches(image):
                self.skip(self.opened_pending, '已打开的未读行发生变化，无法恢复复核，未输入。')
                self.clear_opened()
            elif self.opened_row.highlighted and current_name:
                self.opened_pending = current_name
        current_has_badge = not visual and any(
            numbered_badge(row) and row['conversation'].strip() == current_name and row['confidence'] >= .9
            for row in view['unread'])
        allowed_current = (not unread_only or current_has_badge or self.opened_pending == current_name)
        result = self.reply_current(client, view, persona) if allowed_current else None
        if result:
            return 'sent', result
        self.clear_opened()

        badges = numbered_candidates(image, view['list_box']) if visual else []
        for row in view['unread']:
            if not numbered_badge(row):
                continue
            name = row['conversation'].strip()
            badge = self.badge_at_row(row, image, badges) if visual else None
            if visual and not badge:
                self.skip(name, '对应行缺少可核对的红色数字角标，未点击。')
                continue
            if badge and badge.signature(image) in self.failed_badges:
                continue
            if not badge and name in self.skipped:
                continue
            if not badge and row['confidence'] < .9:
                self.skip(name, '未读标记或会话位置识别不清，未点击。')
                continue
            if not badge and name.endswith(('…', '...', '⋯')):
                self.skip(name, '会话名称被截断，请加宽左侧列表后重试。')
                continue
            if not badge and name == view['conversation'].strip():
                if fingerprint(view) in self.seen:
                    self.skip(name, '与本次运行已处理的消息相同，避免重复发送。')
                continue
            # Re-read immediately before using coordinates from a prior API call.
            current_image, current = self.read_page(client)
            if badge:
                current_badges = numbered_candidates(current_image, current['list_box'])
                target = next((item for item in current['unread'] if numbered_badge(item)
                               and self.badge_at_row(item, current_image, current_badges) == badge), None)
                if target and badge.signature(image) != badge.signature(current_image):
                    target = None
            else:
                target = next((item for item in current['unread'] if numbered_badge(item)
                               and item['conversation'].strip() == name and item['confidence'] >= .9), None)
            if target is None:
                self.cached_digest = None
                return 'status', '未读列表发生变化，正在重新检查本轮列表…'
            # A new message can reorder rows while the model request is running.
            if self.list_digest(current_image, current['list_box']) != self.list_digest(self.capture(), current['list_box']):
                self.cached_digest = None
                return 'status', '会话列表发生重排，正在重新读取未读位置…'
            point = self.point(target['x'], target['y'])
            if badge:
                x, y = badge.point
                point = self.point(x * 1000 / current_image.width, y * 1000 / current_image.height)
            self.desktop.click(self.hwnd, point, self.cancel)
            self.opened_pending = name  # Opening clears the unread badge before an API retry.
            self.stats.opened += 1
            self.wait(.6)
            if badge:
                self.opened_row = OpenedRow.confirm(badge, current_image, self.capture())
                if not self.opened_row:
                    self.failed_badges.add(badge.signature(current_image))
                    self.skip(name, '点击后无法确认原行角标清除且头像一致，未输入。')
                    self.clear_opened()
                    self.cached_digest = None
                    return 'status', '未能复核已点击的未读行，继续检查其他会话…'
            try:
                selected = self.inspect(client)
            except SkipConversation as error:
                self.skip(name, str(error))
                self.clear_opened()
                self.cached_digest = None
                return 'status', '选中的未读行已变化，继续检查其他会话…'
            selected_name = selected['conversation'].strip()
            visually_confirmed = self.opened_row and self.opened_row.highlighted and selected_name
            title_uncertain = selected_name != name or name.endswith(('…', '...', '⋯'))
            if title_uncertain and not visually_confirmed:
                self.skip(name, '打开后的聊天标题不匹配，未输入；继续检查其他未读。')
                self.skip(selected['conversation'], '由一次不匹配的点击打开，未确认目标，未输入。')
                self.clear_opened()
                self.cached_digest = None
                return 'status', '会话定位变化，已记录跳过并继续扫描…'
            if selected_name != name:
                record('conversation_row_verified', conversation=selected_name,
                       details={'sidebar_title': name, 'badge_bounds': badge.bounds})
            self.opened_pending = selected_name
            if fingerprint(selected) in self.seen and fingerprint(selected) not in self.pending_current:
                self.skip(name, '消息已处理，避免重复发送。')
            else:
                result = self.reply_current(client, selected, persona)
                if result:
                    return 'sent', result
                if name not in self.skipped:
                    self.skip(name, '打开后未发现明确的对方新消息。')
            self.clear_opened()
            self.cached_digest = None
            return 'status', '已检查一个未读会话，正在继续处理其他会话…'

        if not allow_scroll:
            return 'idle', None
        probe = not (self.scroll_direction_known and (self.scrolled_down or view.get('list_end_visible', False)))
        _, moved = self.scroll_list(image, view['list_box'], -1, probe_boundary=probe)
        self.bottom_still = 0 if moved else self.bottom_still + 1
        if self.bottom_still >= 2:
            if not self.scroll_boundary_verified and not view.get('list_end_visible', False):
                raise RuntimeError('列表没有响应滚动，尚未确认到底，本轮未完成。请检查列表是否可滚动，'
                                   '并暂停滚动方向或鼠标增强软件后重试。')
            return 'bottom', None
        return 'status', '正在向下检查列表，查找尚未打开的未读会话…'

    def sweep(self, client, persona, report=None):
        """Drain a complete list traversal; retries resume with existing counters."""
        self.report = report or (lambda kind, value: None)
        if not self.sweeping:
            self.stats = SweepStats()
            self.skipped.clear()
            self.failed_badges.clear()
            self.sweeping = True
        self.focus()
        image, view = self.read_page(client)
        if not self.opened_pending:
            self.report('status', '正在将左侧会话列表滚动到顶部，开始检查全部未读…')
            self.go_to_top(image, view['list_box'])
        final_check = False
        for _ in range(10000):
            kind, value = self.poll(client, persona, allow_scroll=not final_check, unread_only=True)
            if kind == 'sent':
                self.report('sent', value)
                image, view = self.read_page(client)
                self.go_to_top(image, view['list_box'])
                final_check = False
            elif kind == 'bottom':
                image, view = self.read_page(client)
                self.go_to_top(image, view['list_box'])
                final_check = True  # Catch new chats moved to the top during the sweep.
            elif kind == 'idle':
                self.sweeping = False
                result = self.stats
                self.report('complete', result)
                record('sweep_complete', checked=result.checked, opened=result.opened, sent=result.sent, skipped=result.skipped)
                return result
            else:
                self.report('status', value + ' ' + self.stats.summary())
        raise RuntimeError('会话列表持续变化，本轮扫描尚未完成。' + self.stats.summary())
