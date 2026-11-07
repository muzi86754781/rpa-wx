import sys
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
import desktop as win
from core import Model, fingerprint
from automation import ConversationSession
from api_errors import Cooldown, run_operation
from settings import SettingsStore
from settings_ui import SettingsDialog
from model_catalog import BUILTIN
import diagnostics
from diagnostics_ui import DiagnosticsDialog
import typing_pace

AUTO_LABEL = '持续监控全部未读会话并回复'


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('WeChat Agent · 视觉回复助手')
        area = win.work_area() if sys.platform == 'win32' else None
        available_height = area[3] - area[1] if area else self.winfo_screenheight()
        self.geometry(f'580x{min(880, max(250, available_height - 65))}')
        self.minsize(380, 250)
        self.configure(bg='#f7f7fc')
        self.hwnd = None
        self.bounds = None
        self.points = {}
        self.pending = None
        self.seen = set()
        self.busy = False
        self.ui_timer = None
        self.input_watch_active = False
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.api_cooldowns = {}
        self.auto = tk.BooleanVar(value=False)
        self.auto_session = None
        self.next_auto_check = 0
        self.empty = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='先绑定微信窗口，再校准输入框和发送按钮。')
        self.settings_store = SettingsStore()
        self.diagnostics_notice = ''
        try:
            diagnostics.setup(self.settings_store.path.parent / 'logs')
        except OSError:
            self.diagnostics_notice = '诊断日志无法初始化，请检查本机目录权限。'
        typing_pace.configure(self.settings_store.typing_interval_ms, self.settings_store.typing_corrections)
        self.settings_window = None
        self.provider_status = tk.StringVar()
        self.persona = tk.StringVar(value='使用简洁自然的中文，语气友好。')
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TButton', padding=8)
        toolbar = ttk.Frame(self, padding=(16, 10))
        toolbar.pack(fill='x')
        header = ttk.Frame(toolbar)
        header.pack(fill='x')
        self.stop_button = ttk.Button(header, text='停止 (Esc)', command=self.stop)
        self.stop_button.pack(side='right')
        self.settings_button = ttk.Button(header, text='设置', command=self.open_settings)
        self.settings_button.pack(side='right', padx=4)
        self.logs_button = ttk.Button(header, text='日志', command=self.open_logs)
        self.logs_button.pack(side='right')
        ttk.Label(header, text='WeChat Agent', font=('Microsoft YaHei UI', 17, 'bold')).pack(side='left')
        self.status_label = ttk.Label(toolbar, textvariable=self.status, wraplength=540, foreground='#6b4dcc')
        self.status_label.pack(fill='x', pady=(8, 0))
        self.status_label.bind('<Configure>', lambda event: self.status_label.configure(wraplength=event.width))

        content = ttk.Frame(self)
        content.pack(fill='both', expand=True)
        self.scroll_area = tk.Canvas(content, highlightthickness=0, background=style.lookup('TFrame', 'background'))
        scrollbar = ttk.Scrollbar(content, orient='vertical', command=self.scroll_area.yview)
        scrollbar.pack(side='right', fill='y')
        self.scroll_area.configure(yscrollcommand=scrollbar.set)
        self.scroll_area.pack(side='left', fill='both', expand=True)
        body = ttk.Frame(self.scroll_area, padding=(16, 0, 16, 12))
        body_id = self.scroll_area.create_window((0, 0), window=body, anchor='nw')
        body.bind('<Configure>', lambda event: self.scroll_area.configure(scrollregion=self.scroll_area.bbox(body_id)))
        self.scroll_area.bind('<Configure>', lambda event: self.scroll_area.itemconfigure(body_id, width=event.width))
        self.bind('<MouseWheel>', self.scroll_controls)
        self.bind('<Escape>', lambda event: self.stop())
        ttk.Label(body, text='数字红角标会话批量回复 / 当前聊天人工审核', foreground='#77718e').pack(anchor='w', pady=(2, 15))
        ttk.Label(body, textvariable=self.provider_status, foreground='#77718e').pack(anchor='w', pady=(0, 10))
        ttk.Label(body, text='回复风格 / 业务要求').pack(anchor='w')
        ttk.Entry(body, textvariable=self.persona).pack(fill='x', pady=(2, 7))
        row = ttk.Frame(body)
        row.pack(fill='x', pady=6)
        for label, command in [('绑定窗口', self.bind_window),
                                ('校准输入框', lambda: self.calibrate('input')),
                                ('校准发送按钮', lambda: self.calibrate('send'))]:
            ttk.Button(row, text=label, command=command).pack(side='left', padx=2)
        ttk.Label(body, text='绑定：自动查找微信主窗口。校准：倒计时内将鼠标停在目标位置。', wraplength=520).pack(anchor='w')
        self.history = tk.Text(body, height=12, wrap='word', bg='#ffffff', relief='flat',
                               padx=12, pady=12, font=('Microsoft YaHei UI', 10), state='disabled')
        self.history.pack(fill='both', expand=True, pady=12)
        self.batch_button = ttk.Button(body, text='检查全部未读并回复', command=self.scan)
        self.batch_button.pack(fill='x', pady=6)
        self.auto_button = ttk.Button(body, text=AUTO_LABEL, command=self.toggle_auto)
        self.auto_button.pack(fill='x', pady=6)
        ttk.Label(body, text='仅处理红色圆形数字角标；小步滚动检查整个列表。运行时请暂停其他操作。',
                  wraplength=520, foreground='#777777').pack(anchor='w')
        ttk.Button(body, text='仅为当前聊天生成草稿', command=self.draft_current).pack(fill='x', pady=6)
        ttk.Label(body, text='待审核回复 · 可编辑').pack(anchor='w')
        self.draft = tk.Text(body, height=5, wrap='word', padx=10, pady=8,
                             font=('Microsoft YaHei UI', 11), relief='solid', borderwidth=1)
        self.draft.pack(fill='x', pady=5)
        ttk.Checkbutton(body, text='已确认微信输入框为空，且 Shift+Enter 为换行', variable=self.empty).pack(anchor='w')
        row = ttk.Frame(body)
        row.pack(fill='x', pady=8)
        self.send_button = ttk.Button(row, text='审核通过 · 逐字输入并发送', command=self.send)
        self.send_button.pack(side='left', expand=True, fill='x')
        stop_hint = '运行时移动鼠标或按住 Esc 可中止。' if sys.platform == 'win32' else '输入时按住 Esc 可中止。'
        ttk.Label(body, text=stop_hint + '截图将发送至设置中的模型服务。',
                  wraplength=500, foreground='#777777').pack(anchor='w', pady=5)
        self.after(100, self.drain)
        self.after(1000, self.tick)
        if sys.platform == 'win32':
            self.after(35, self.watch_interruptions)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh_provider()
        if self.settings_store.notice or self.diagnostics_notice:
            self.status.set(self.settings_store.notice or self.diagnostics_notice)

    def scroll_controls(self, event):
        if not event.delta or isinstance(event.widget, (tk.Text, ttk.Combobox)):
            return
        if sys.platform == 'win32':
            steps = max(1, abs(event.delta) // 120)
        else:
            steps = abs(event.delta)
        self.scroll_area.yview_scroll(-steps if event.delta > 0 else steps, 'units')

    def open_logs(self):
        if self.busy or self.auto.get():
            self.stop()
        return DiagnosticsDialog(self)

    def report_callback_exception(self, exception, value, tb):
        diagnostics.record_exception('ui_callback', value.with_traceback(tb))
        self.stop()
        self.status.set('界面操作发生错误，可点击右上角“日志”导出诊断包。')

    def refresh_provider(self):
        profile = self.settings_store.current
        self.provider_status.set('模型服务：' + profile.label + (' · 已配置' if profile.key else ' · 请在右上角设置 API Key'))

    def open_settings(self):
        if self.busy or self.auto.get():
            self.status.set('请先停止当前操作，再修改模型设置。')
            return
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        self.settings_window = SettingsDialog(self, self.settings_store, self.settings_saved)

    def settings_saved(self):
        typing_pace.configure(self.settings_store.typing_interval_ms, self.settings_store.typing_corrections)
        self.refresh_provider()
        self.status.set('已保存模型服务设置；API Key 仅在本次运行有效。')

    def create_client(self):
        profile = self.settings_store.current.validated()
        return Model(profile.key, profile.vision_model, reply_model=profile.reply_model,
                     base_url=profile.base_url, protocol=profile.protocol,
                     provider=profile.provider, json_mode=profile.json_mode)

    def log(self, text):
        self.history.configure(state='normal')
        self.history.insert('end', text + '\n\n')
        self.history.see('end')
        self.history.configure(state='disabled')

    def countdown(self, label, done, n=4):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        def step(left):
            self.ui_timer = None
            self.status.set(f'{label}：{left} 秒')
            if left:
                self.ui_timer = self.after(1000, lambda: step(left - 1))
            else:
                try:
                    done()
                    self.status.set(label + '完成')
                except Exception as error:
                    diagnostics.record_exception('calibration', error)
                    self.status.set(str(error))
                finally:
                    self.busy = False
        step(n)

    def bind_window(self):
        if self.busy or self.auto.get():
            self.status.set('请先停止当前操作，再绑定微信。')
            return
        def done():
            win.permissions()
            hwnd = win.find_wechat()
            if not hwnd or not win.is_wechat(hwnd):
                raise ValueError('未找到可识别的微信主窗口。请打开已登录的桌面微信。')
            win.activate(hwnd)
            self.hwnd, self.bounds = hwnd, win.rect(hwnd)
            self.points.clear()
            self.pending = None
            self.draft.delete('1.0', 'end')
            self.empty.set(False)
            self.log('已绑定：' + win.title(hwnd))
        self.busy = True
        self.cancel.clear()
        self.status.set('正在查找微信主窗口…')
        def bind():
            self.ui_timer = None
            try:
                done()
                self.status.set('已绑定微信，请校准输入框和发送按钮。')
            except Exception as error:
                diagnostics.record_exception('bind_window', error)
                self.status.set(str(error))
            finally:
                self.busy = False
        self.ui_timer = self.after(100, bind)

    def calibrate(self, name):
        if self.auto.get():
            self.status.set('请先停止自动回复，再校准。')
            return
        def done():
            self.validate()
            x, y = win.cursor()
            l, t, r, b = self.bounds
            if not (l < x < r and t < y < b):
                raise ValueError('鼠标不在绑定窗口内。')
            self.points[name] = (x, y)
        self.countdown('请将鼠标停在' + ('输入框' if name == 'input' else '发送按钮'), done)

    def validate(self):
        if not self.hwnd or win.rect(self.hwnd) != self.bounds:
            raise ValueError('窗口未绑定或位置/尺寸改变，请重新绑定和校准。')

    def capture(self):
        self.validate()
        win.check(self.hwnd, self.cancel)
        return win.capture(self.hwnd)

    def launch(self, work, prepare_retry=None):
        if self.busy:
            return
        try:
            client = self.create_client()
        except ValueError as error:
            self.status.set(str(error))
            return
        self.busy = True
        if not self.auto.get():
            self.cancel.clear()
        if sys.platform == 'win32' and not self.input_watch_active:
            win.start_input_watch(self.cancel)
            self.input_watch_active = True
        identity = (client.provider, client.base_url, client.key)
        cooldown = self.api_cooldowns.setdefault(identity, Cooldown())
        def run():
            try:
                run_operation(lambda: work(client), self.cancel, cooldown,
                              lambda text: self.events.put(('status', text)), prepare_retry)
            except Exception as error:
                # API errors contain only fixed diagnostics, never raw response bodies.
                diagnostics.record_exception('worker', error)
                self.events.put(('error', str(error)))
            finally:
                self.events.put(('done', None))
        threading.Thread(target=run, daemon=True).start()

    def focus(self):
        if self.cancel.is_set():
            raise RuntimeError('操作已停止。')
        self.validate()
        win.activate(self.hwnd)
        if self.cancel.wait(.5):
            raise RuntimeError('操作已停止。')
        win.check(self.hwnd, self.cancel)

    def validate_batch(self):
        self.validate()
        if set(self.points) != {'input', 'send'}:
            raise ValueError('请先校准输入框和发送按钮，再检查全部未读并回复。')
        self.create_client()
        if self.pending or self.draft.get('1.0', 'end').strip():
            if not self.draft.get('1.0', 'end').strip():
                self.pending = None
            else:
                raise ValueError('请先处理或清空助手里的待审核草稿，再批量回复未读会话。')

    def scan(self):
        if self.busy or self.auto.get():
            self.status.set('正在检查未读列表，请等待本轮结束或点击停止。')
            return
        try:
            self.validate_batch()
        except Exception as error:
            self.status.set(str(error))
            return
        session = ConversationSession(win, self.hwnd, self.bounds, self.points,
                                      self.cancel, self.seen)
        self.status.set('准备检查全部未读会话并回复…')
        self.launch_sweep(session)

    def launch_sweep(self, session):
        persona = self.persona.get()
        def work(client):
            session.sweep(client, persona,
                          lambda kind, value: self.events.put(('batch_' + kind, value)))
        self.launch(work, prepare_retry=session.prepare_retry)

    def draft_current(self):
        if self.busy or self.auto.get():
            self.status.set('未读列表检查运行中；生成当前聊天草稿前请先停止。')
            return
        if self.pending or self.draft.get('1.0', 'end').strip():
            self.status.set('已有待审核草稿；请发送或清空草稿后重新识别。')
            if not self.draft.get('1.0', 'end').strip():
                self.pending = None
            return
        persona = self.persona.get()
        def work(client):
            self.focus()
            view = client.inspect(self.capture())
            if self.cancel.is_set():
                return
            if not view['incoming'] or view['confidence'] < .9 or not all(fingerprint(view)):
                self.events.put(('status', '没有明确可回复的新消息，或识别置信度不足。'))
                return
            if fingerprint(view) in self.seen:
                self.events.put(('status', '该消息已处理，跳过重复回复。'))
                return
            reply = client.reply(view, persona)
            if not self.cancel.is_set():
                self.events.put(('draft', (view, reply)))
        self.status.set('正在识别并生成回复…')
        self.launch(work, prepare_retry=lambda: True)

    def send(self):
        if self.busy or self.auto.get():
            self.status.set('正在处理未读会话；手动发送前请先停止。')
            return
        text = self.draft.get('1.0', 'end-1c').strip()
        view = self.pending
        if not view or not text or not self.empty.get() or set(self.points) != {'input', 'send'}:
            self.status.set('请先生成草稿、完成两项校准，并确认输入框为空。')
            return
        if len(text) > 2000:
            self.status.set('回复超过 2000 字，请精简。')
            return
        def work(client):
            ConversationSession(win, self.hwnd, self.bounds, self.points,
                                self.cancel, self.seen).send(client, view, text)
            self.events.put(('sent', (view, text)))
        self.status.set('复核会话 → 逐字输入 → 复核 → 点击发送…')
        self.launch(work)

    def toggle_auto(self):
        if self.auto.get():
            self.stop()
            return
        if self.busy:
            self.status.set('请等待当前操作完成。')
            return
        try:
            self.validate_batch()
        except Exception as error:
            self.status.set(str(error))
            return
        self.cancel.clear()
        self.auto.set(True)
        self.auto_button.configure(text='停止持续监控')
        self.auto_session = ConversationSession(win, self.hwnd, self.bounds, self.points,
                                                self.cancel, self.seen)
        self.log('持续监控已开启：每轮检查整个列表，只打开带红色圆形数字角标的会话并回复。')
        self.poll_auto()

    def poll_auto(self):
        self.launch_sweep(self.auto_session)

    def release_input_watch(self):
        if self.input_watch_active:
            win.end_input_watch(self.cancel)
            self.input_watch_active = False

    def check_interruption(self):
        reason = win.poll_input_watch(self.cancel) if self.input_watch_active else None
        if reason:
            self.stop(reason)
        elif self.ui_timer is not None and win.escape_pressed():
            self.stop('已按 Esc 停止。')

    def watch_interruptions(self):
        self.check_interruption()
        self.after(35, self.watch_interruptions)

    def stop(self, reason='已停止后续操作。'):
        self.cancel.set()
        if self.ui_timer is not None:
            self.after_cancel(self.ui_timer)
            self.ui_timer = None
            self.busy = False
        self.release_input_watch()
        self.auto.set(False)
        self.auto_button.configure(text=AUTO_LABEL)
        self.status.set(reason + (' 正在结束本轮。' if self.busy else ''))
        diagnostics.record('operation_stopped', reason=reason)

    def drain(self):
        if sys.platform == 'win32':
            self.check_interruption()
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'done':
                    self.busy = False
                    if not self.auto.get():
                        self.release_input_watch()
                elif kind == 'draft' and not self.cancel.is_set():
                    self.pending, reply = value
                    self.log(self.pending['conversation'] + '：' + self.pending['message'])
                    self.draft.delete('1.0', 'end')
                    self.draft.insert('1.0', reply)
                    self.status.set('回复已生成，请审核。')
                elif kind in ('sent', 'batch_sent'):
                    view, text = value
                    self.seen.add(fingerprint(view))
                    self.log(view['conversation'] + ' · 已点击发送（请查看微信确认）：' + text)
                    if kind == 'sent':
                        self.pending = None
                        self.draft.delete('1.0', 'end')
                    self.empty.set(False)
                    if not self.cancel.is_set():
                        self.status.set('已点击发送。')
                elif kind == 'error':
                    self.auto.set(False)
                    self.auto_button.configure(text=AUTO_LABEL)
                    self.empty.set(False)
                    self.log(str(value))
                    if not self.cancel.is_set():
                        self.status.set(value)
                elif kind == 'batch_skip':
                    name, reason = value
                    self.log('跳过 ' + name + '：' + reason)
                elif kind == 'batch_status' and not self.cancel.is_set():
                    self.status.set(value)
                elif kind == 'batch_complete':
                    text = '本轮未读列表检查完成。' + value.summary()
                    self.log(text)
                    self.next_auto_check = time.monotonic() + 5
                    if not self.cancel.is_set():
                        self.status.set(text + (' 5 秒后开始下一轮。' if self.auto.get() else ''))
                elif kind == 'status' and not self.cancel.is_set():
                    self.status.set(value)
        except queue.Empty:
            pass
        self.after(100, self.drain)

    def tick(self):
        if self.auto.get() and not self.cancel.is_set() and not self.busy and time.monotonic() >= self.next_auto_check:
            self.poll_auto()
        self.after(1000, self.tick)

    def close(self):
        self.cancel.set()
        self.release_input_watch()
        diagnostics.record('app_close')
        diagnostics.close()
        self.destroy()


if __name__ == '__main__':
    if '--smoke-test' in sys.argv:
        app = App()
        app.update()
        result = {'ok': True, 'platform': sys.platform, 'ui': [app.winfo_width(), app.winfo_height()]}
        assert app.batch_button.winfo_ismapped() and app.auto_button.winfo_ismapped()
        result['reply_scope'] = 'all_unread'
        assert app.logs_button.winfo_ismapped()
        initial_geometry = app.geometry()
        app.geometry('500x420')
        app.update()
        calibration_completed = []
        app.countdown('校准输入框', lambda: calibration_completed.append(True))
        app.update()
        assert app.status.get() == '校准输入框：4 秒'
        for control in (app.status_label, app.stop_button):
            assert control.winfo_ismapped()
            assert control.winfo_rooty() + control.winfo_height() <= app.scroll_area.winfo_rooty()
        app.scroll_area.yview_moveto(1)
        app.update()
        assert app.scroll_area.yview()[0] > 0
        assert app.scroll_area.winfo_rooty() <= app.send_button.winfo_rooty()
        assert app.send_button.winfo_rooty() + app.send_button.winfo_height() <= app.winfo_rooty() + app.winfo_height()
        app.stop_button.invoke()
        assert not app.busy and app.ui_timer is None and not calibration_completed
        app.scroll_area.yview_moveto(0)
        app.geometry(initial_geometry)
        app.update()
        result['compact_controls'] = True
        log_dialog = app.open_logs()
        app.update()
        assert log_dialog.export_button.winfo_ismapped()
        log_dialog.destroy()
        result['diagnostics_ui'] = True
        app.open_settings()
        app.update()
        dialog = app.settings_window
        assert dialog.winfo_exists() and app.settings_button.winfo_ismapped()
        dialog.notebook.select(1)
        app.update()
        assert dialog.input_save_button.winfo_ismapped()
        assert 80 <= int(dialog.typing_interval.get()) <= 800
        dialog.notebook.select(0)
        app.update()
        dialog.key.set('smoke-test-placeholder')
        dialog.provider.set('阿里云百炼（北京）')
        dialog.select_provider()
        app.update()
        assert dialog.key.get() != 'smoke-test-placeholder'
        assert dialog.vision_model.get() == app.settings_store.profiles['aliyun-beijing'].vision_model
        assert dialog.protocol.get() == 'Chat Completions（兼容接口）'
        location = dialog.catalog_location()
        if location is not None:
            dialog.catalogs[location] = BUILTIN
        browser = dialog.browse_models('vision')
        app.update()
        assert len(BUILTIN) >= 500
        browser.query.set('qwen3.8 max')
        app.update()
        assert browser.matches and all('vision' in row.roles for row in browser.matches)
        browser.show_all.set(True)
        browser.query.set('qwen-image-3.0-pro')
        browser.refresh()
        app.update()
        assert browser.matches and not browser.matches[0].roles
        browser.tree.selection_set('0')
        browser.selection_changed()
        assert str(browser.choose_button['state']) == 'disabled'
        browser.close()
        assert dialog.save_button.winfo_ismapped()
        assert dialog.save_button.winfo_rooty() + dialog.save_button.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()
        result['settings_ui'] = [dialog.winfo_width(), dialog.winfo_height()]
        result['catalog_models'] = len(BUILTIN)
        dialog.destroy()
        if '--smoke-output' in sys.argv:
            Path(sys.argv[sys.argv.index('--smoke-output') + 1]).write_text(json.dumps(result), encoding='utf-8')
        else:
            print(json.dumps(result))
        app.destroy()
    else:
        App().mainloop()
