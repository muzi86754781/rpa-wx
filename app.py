import os
import sys
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk
import desktop as win
from core import Model, fingerprint


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('WeChat Agent · 视觉回复助手')
        self.geometry('560x820')
        self.configure(bg='#f7f7fc')
        self.hwnd = None
        self.bounds = None
        self.points = {}
        self.pending = None
        self.seen = set()
        self.busy = False
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.monitor = tk.BooleanVar(value=False)
        self.empty = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='先绑定微信窗口，再校准输入框和发送按钮。')
        self.key = tk.StringVar(value=os.environ.get('OPENAI_API_KEY', ''))
        self.model = tk.StringVar(value='gpt-6.1-terra')
        self.persona = tk.StringVar(value='使用简洁自然的中文，语气友好。')
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TButton', padding=8)
        body = ttk.Frame(self, padding=18)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='◉  WeChat Agent', font=('Microsoft YaHei UI', 21, 'bold')).pack(anchor='w')
        ttk.Label(body, text='GPT-6.1 Terra  /  当前会话 · 人工审核', foreground='#77718e').pack(anchor='w', pady=(2, 15))
        for label, variable, secret in [('OpenAI API Key（仅内存）', self.key, True),
                                        ('模型 ID', self.model, False),
                                        ('回复风格 / 业务要求', self.persona, False)]:
            ttk.Label(body, text=label).pack(anchor='w')
            ttk.Entry(body, textvariable=variable, show='•' if secret else '').pack(fill='x', pady=(2, 7))
        row = ttk.Frame(body)
        row.pack(fill='x', pady=6)
        for label, command in [('绑定窗口', self.bind_window),
                                ('校准输入框', lambda: self.calibrate('input')),
                                ('校准发送按钮', lambda: self.calibrate('send'))]:
            ttk.Button(row, text=label, command=command).pack(side='left', padx=2)
        ttk.Label(body, text='绑定：倒计时内切到微信。校准：将鼠标停在目标位置。', wraplength=500).pack(anchor='w')
        self.history = tk.Text(body, height=12, wrap='word', bg='#ffffff', relief='flat',
                               padx=12, pady=12, font=('Microsoft YaHei UI', 10), state='disabled')
        self.history.pack(fill='both', expand=True, pady=12)
        ttk.Checkbutton(body, text='每 10 秒检查当前会话（仅微信在前台时）', variable=self.monitor).pack(anchor='w')
        ttk.Button(body, text='识别最新消息 → 生成回复', command=self.scan).pack(fill='x', pady=6)
        ttk.Label(body, text='待审核回复 · 可编辑').pack(anchor='w')
        self.draft = tk.Text(body, height=5, wrap='word', padx=10, pady=8,
                             font=('Microsoft YaHei UI', 11), relief='solid', borderwidth=1)
        self.draft.pack(fill='x', pady=5)
        ttk.Checkbutton(body, text='已确认微信输入框为空，且 Shift+Enter 为换行', variable=self.empty).pack(anchor='w')
        row = ttk.Frame(body)
        row.pack(fill='x', pady=8)
        ttk.Button(row, text='审核通过 · 逐字输入并发送', command=self.send).pack(side='left', expand=True, fill='x')
        ttk.Button(row, text='停止', command=self.stop).pack(side='left', padx=(8, 0))
        ttk.Label(body, textvariable=self.status, wraplength=500, foreground='#6b4dcc').pack(anchor='w')
        ttk.Label(body, text='输入时按住 Esc 可中止。截图将发送至 OpenAI；不保存密钥或截图。',
                  wraplength=500, foreground='#777777').pack(anchor='w', pady=5)
        self.after(100, self.drain)
        self.after(10000, self.tick)
        self.protocol('WM_DELETE_WINDOW', self.close)

    def log(self, text):
        self.history.configure(state='normal')
        self.history.insert('end', text + '\n\n')
        self.history.see('end')
        self.history.configure(state='disabled')

    def countdown(self, label, done, n=4):
        if self.busy:
            return
        self.busy = True
        def step(left):
            self.status.set(f'{label}：{left} 秒')
            if left:
                self.after(1000, lambda: step(left - 1))
            else:
                try:
                    done()
                    self.status.set(label + '完成')
                except Exception as error:
                    self.status.set(str(error))
                finally:
                    self.busy = False
        step(n)

    def bind_window(self):
        def done():
            win.permissions()
            hwnd = win.foreground()
            if not hwnd or not win.is_wechat(hwnd):
                raise ValueError('前台不是可识别的微信窗口。')
            self.hwnd, self.bounds = hwnd, win.rect(hwnd)
            self.points.clear()
            self.pending = None
            self.log('已绑定：' + win.title(hwnd))
        self.countdown('请切换到微信窗口', done)

    def calibrate(self, name):
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

    def launch(self, work):
        if self.busy:
            return
        if not self.key.get().strip():
            self.status.set('请填写 OpenAI API Key。')
            return
        self.busy = True
        self.cancel.clear()
        client = Model(self.key.get().strip(), self.model.get().strip())
        def run():
            try:
                work(client)
            except Exception as error:
                # HTTP errors deliberately omit response bodies and credentials.
                self.events.put(('error', str(error)))
            finally:
                self.events.put(('done', None))
        threading.Thread(target=run, daemon=True).start()

    def focus(self):
        self.validate()
        win.activate(self.hwnd)
        if self.cancel.wait(.5):
            raise RuntimeError('操作已停止。')
        win.check(self.hwnd, self.cancel)

    def scan(self):
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
        self.launch(work)

    def send(self):
        text = self.draft.get('1.0', 'end-1c').strip()
        view = self.pending
        if not view or not text or not self.empty.get() or set(self.points) != {'input', 'send'}:
            self.status.set('请先生成草稿、完成两项校准，并确认输入框为空。')
            return
        if len(text) > 2000:
            self.status.set('回复超过 2000 字，请精简。')
            return
        def work(client):
            self.focus()
            fresh = client.inspect(self.capture())
            if (fingerprint(fresh) != fingerprint(view) or not fresh['incoming']
                    or fresh['confidence'] < .9):
                raise ValueError('会话或最新消息已变化，请清空草稿并重新识别。')
            self.validate()
            win.click(self.hwnd, self.points['input'], self.cancel)
            win.type_text(self.hwnd, text, self.cancel)
            # Re-read after typing so a changed conversation cannot be sent blindly.
            fresh = client.inspect(self.capture())
            if (fingerprint(fresh) != fingerprint(view) or not fresh['incoming']
                    or fresh['confidence'] < .9):
                raise ValueError('输入期间消息变化，已保留微信草稿，请手动检查。')
            self.validate()
            win.click(self.hwnd, self.points['send'], self.cancel)
            self.events.put(('sent', (view, text)))
        self.status.set('复核会话 → 逐字输入 → 复核 → 点击发送…')
        self.launch(work)

    def stop(self):
        self.cancel.set()
        self.monitor.set(False)
        self.status.set('已请求停止。正在进行的 API 请求结束后丢弃结果。')

    def drain(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'done':
                    self.busy = False
                elif kind == 'draft' and not self.cancel.is_set():
                    self.pending, reply = value
                    self.log(self.pending['conversation'] + '：' + self.pending['message'])
                    self.draft.delete('1.0', 'end')
                    self.draft.insert('1.0', reply)
                    self.status.set('回复已生成，请审核。')
                elif kind == 'sent':
                    view, text = value
                    self.seen.add(fingerprint(view))
                    self.log('已点击发送（请查看微信确认）：' + text)
                    self.pending = None
                    self.draft.delete('1.0', 'end')
                    self.empty.set(False)
                    self.status.set('已点击发送。')
                elif kind == 'error':
                    self.monitor.set(False)
                    self.empty.set(False)
                    self.status.set(value)
                elif kind == 'status':
                    self.status.set(value)
        except queue.Empty:
            pass
        self.after(100, self.drain)

    def tick(self):
        if (self.monitor.get() and not self.busy and not self.pending
                and self.hwnd and win.foreground() == self.hwnd):
            self.scan()
        self.after(10000, self.tick)

    def close(self):
        self.cancel.set()
        self.destroy()


if __name__ == '__main__':
    if '--smoke-test' in sys.argv:
        app = App()
        app.update()
        result = {'ok': True, 'platform': sys.platform, 'ui': [app.winfo_width(), app.winfo_height()]}
        if '--smoke-output' in sys.argv:
            Path(sys.argv[sys.argv.index('--smoke-output') + 1]).write_text(json.dumps(result), encoding='utf-8')
        else:
            print(json.dumps(result))
        app.destroy()
    else:
        App().mainloop()
