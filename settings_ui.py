"""Provider configuration dialog. No API calls occur while editing settings."""
from dataclasses import replace
import tkinter as tk
from tkinter import ttk
import webbrowser

from settings import PRESETS, QUOTA_HELP
from model_catalog import BUILTIN, cache_path, catalog_ids, load_catalog
from catalog_ui import ModelBrowser

PROTOCOLS = {'Responses（OpenAI）': 'responses', 'Chat Completions（兼容接口）': 'chat'}
JSON_MODES = {'JSON Schema（严格结构）': 'schema', 'JSON Object（兼容模式）': 'object',
              '提示词 JSON（服务商无 JSON 模式时）': 'prompt'}


class SettingsDialog(tk.Toplevel):
    def __init__(self, master, store, on_saved):
        super().__init__(master)
        self.store, self.on_saved = store, on_saved
        self.title('设置 · 模型服务')
        self.geometry('660x720')
        self.resizable(False, False)
        self.transient(master)
        self.provider = tk.StringVar()
        self.base_url = tk.StringVar()
        self.protocol = tk.StringVar()
        self.vision_model = tk.StringVar()
        self.reply_model = tk.StringVar()
        self.json_mode = tk.StringVar()
        self.key = tk.StringVar()
        self.show_key = tk.BooleanVar(value=False)
        self.note = tk.StringVar()
        self.error = tk.StringVar()
        self.catalogs = {}
        self.catalog_status = tk.StringVar()
        self.typing_interval = tk.StringVar(value=str(store.typing_interval_ms))
        self.typing_corrections = tk.BooleanVar(value=store.typing_corrections)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True)
        model_tab, input_tab = ttk.Frame(self.notebook), ttk.Frame(self.notebook, padding=22)
        self.notebook.add(model_tab, text='模型服务')
        self.notebook.add(input_tab, text='输入节奏与诊断')
        ttk.Label(input_tab, text='输入节奏', font=('', 18, 'bold')).pack(anchor='w', pady=(0, 15))
        ttk.Label(input_tab, text='每字基准间隔（80–800 毫秒，默认 180）').pack(anchor='w')
        ttk.Spinbox(input_tab, from_=80, to=800, increment=20, textvariable=self.typing_interval).pack(fill='x', pady=8)
        ttk.Checkbutton(input_tab, text='偶尔输入临时错字，立即删除后重输', variable=self.typing_corrections).pack(anchor='w')
        ttk.Label(input_tab, text='逐字输入含短暂停顿，标点和每段文字后稍作停顿。临时错字只在输入框内纠正；'
                  '发送前必须复核最终文本。停止时可能留有草稿，请人工检查。', wraplength=590).pack(anchor='w', pady=12)
        ttk.Separator(input_tab).pack(fill='x', pady=14)
        ttk.Label(input_tab, text='主界面右上角“日志”可查看或导出诊断包。日志记录会话标题、校验原因、坐标、模型耗时和程序错误；'
                  '不记录聊天正文、回复正文、API Key 或截图。日志在本机轮转保留，最多约 8 MB。', wraplength=590).pack(anchor='w')
        self.input_save_button = ttk.Button(input_tab, text='保存', command=self.save)
        self.input_save_button.pack(anchor='e', pady=20)
        body = ttk.Frame(model_tab, padding=22)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='模型服务设置', font=('', 18, 'bold')).pack(anchor='w', pady=(0, 12))
        ttk.Label(body, text='服务商').pack(anchor='w')
        self.provider_entry = ttk.Combobox(body, textvariable=self.provider, state='readonly',
                                          values=[value['label'] for value in PRESETS.values()])
        self.provider_entry.pack(fill='x', pady=(3, 10))
        self.provider_entry.bind('<<ComboboxSelected>>', self.select_provider)
        ttk.Label(body, text='API Key（仅内存，退出后清除）').pack(anchor='w')
        key_row = ttk.Frame(body)
        key_row.pack(fill='x', pady=(3, 10))
        self.key_entry = ttk.Entry(key_row, textvariable=self.key, show='•')
        self.key_entry.pack(side='left', fill='x', expand=True)
        ttk.Checkbutton(key_row, text='显示', variable=self.show_key,
                        command=lambda: self.key_entry.configure(show='' if self.show_key.get() else '•')).pack(side='right')
        ttk.Label(body, text='Base URL（可填写业务空间专属地址）').pack(anchor='w')
        ttk.Entry(body, textvariable=self.base_url).pack(fill='x', pady=(3, 10))
        ttk.Label(body, text='截图识别模型 · 必须支持图片输入').pack(anchor='w')
        vision_row = ttk.Frame(body)
        vision_row.pack(fill='x', pady=(3, 10))
        self.vision_entry = ttk.Combobox(vision_row, textvariable=self.vision_model)
        self.vision_entry.pack(side='left', fill='x', expand=True)
        self.vision_browse = ttk.Button(vision_row, text='浏览 / 搜索', command=lambda: self.browse_models('vision'))
        self.vision_browse.pack(side='right', padx=(8, 0))
        ttk.Label(body, text='回复模型 · 可使用文本模型').pack(anchor='w')
        reply_row = ttk.Frame(body)
        reply_row.pack(fill='x', pady=(3, 10))
        self.reply_entry = ttk.Combobox(reply_row, textvariable=self.reply_model)
        self.reply_entry.pack(side='left', fill='x', expand=True)
        self.reply_browse = ttk.Button(reply_row, text='浏览 / 搜索', command=lambda: self.browse_models('reply'))
        self.reply_browse.pack(side='right', padx=(8, 0))
        catalog_row = ttk.Frame(body)
        catalog_row.pack(fill='x', pady=(0, 10))
        self.catalog_button = ttk.Button(catalog_row, text='百炼全部模型目录', command=lambda: self.browse_models(None))
        self.catalog_button.pack(side='left')
        ttk.Label(catalog_row, textvariable=self.catalog_status).pack(side='left', padx=8)
        row = ttk.Frame(body)
        row.pack(fill='x')
        for label, variable, choices in [('接口协议', self.protocol, PROTOCOLS),
                                          ('识别结果格式', self.json_mode, JSON_MODES)]:
            column = ttk.Frame(row)
            column.pack(side='left', fill='x', expand=True, padx=(0, 8))
            ttk.Label(column, text=label).pack(anchor='w')
            ttk.Combobox(column, textvariable=variable, state='readonly',
                         values=list(choices)).pack(fill='x', pady=(3, 10))
        ttk.Label(body, textvariable=self.note, wraplength=610, foreground='#625e72').pack(anchor='w', pady=(0, 8))
        ttk.Label(body, textvariable=self.error, wraplength=610, foreground='#b42318').pack(anchor='w')
        actions = ttk.Frame(body)
        actions.pack(side='bottom', fill='x', pady=(12, 0))
        self.console_button = ttk.Button(actions, text='服务商控制台', command=self.open_console)
        self.console_button.pack(side='left')
        self.quota_button = ttk.Button(actions, text='百炼免费额度说明', command=lambda: webbrowser.open(QUOTA_HELP))
        self.quota_button.pack(side='left', padx=6)
        self.save_button = ttk.Button(actions, text='保存', command=self.save)
        self.save_button.pack(side='right')
        ttk.Button(actions, text='取消', command=self.destroy).pack(side='right', padx=6)
        self.provider.set(self.store.current.label)
        self.select_provider()
        self.bind('<Escape>', lambda event: self.destroy())
        self.grab_set()

    def provider_id(self):
        return next(name for name, preset in PRESETS.items() if preset['label'] == self.provider.get())

    def select_provider(self, event=None):
        provider = self.provider_id()
        profile = self.store.profiles[provider]
        self.base_url.set(profile.base_url)
        self.vision_model.set(profile.vision_model)
        self.reply_model.set(profile.reply_model)
        self.protocol.set(next(label for label, value in PROTOCOLS.items() if value == profile.protocol))
        self.json_mode.set(next(label for label, value in JSON_MODES.items() if value == profile.json_mode))
        self.key.set(profile.key)
        self.show_key.set(False)
        self.key_entry.configure(show='•')
        self.error.set('')
        aliyun = provider.startswith('aliyun-')
        self.fill_model_choices()
        for button in (self.vision_browse, self.reply_browse, self.catalog_button):
            button.configure(state='normal' if aliyun else 'disabled')
        note = '模型和接入地址会保存到本机；密钥仅在本次运行有效。截图会发往当前选择的模型服务。'
        if aliyun:
            note += '\nAPI Key 需与地域匹配。中国站新人免费额度适用北京地域，有效期和余额以控制台为准；建议开启“免费额度用完即停”。'
        elif provider == 'custom':
            note += '\n填入兼容服务的 Base URL 和模型 ID。识别模型需支持图片；若不支持 JSON 格式参数，可选择提示词 JSON。'
        self.note.set(note)
        self.console_button.configure(state='normal' if PRESETS[provider]['console'] else 'disabled')
        self.quota_button.configure(state='normal' if aliyun else 'disabled')

    def catalog_location(self):
        try:
            return cache_path(self.store.path, self.base_url.get())
        except ValueError:
            return None

    def current_catalog(self):
        location = self.catalog_location()
        if location is None:
            return BUILTIN
        if location not in self.catalogs:
            self.catalogs[location] = load_catalog(location)
        return self.catalogs[location]

    def fill_model_choices(self):
        provider = self.provider_id()
        aliyun = provider.startswith('aliyun-')
        rows = self.current_catalog() if aliyun else ()
        self.vision_entry.configure(values=catalog_ids(rows, 'vision') if aliyun else
                                    ['gpt-6.1-terra'] if provider == 'openai' else [])
        self.reply_entry.configure(values=catalog_ids(rows, 'reply') if aliyun else
                                   ['gpt-6.1-terra'] if provider == 'openai' else [])
        self.catalog_status.set(f'{len(rows)} 个模型 · 支持分类搜索' if aliyun else '')

    def browse_models(self, role):
        location = self.catalog_location()
        def on_choose(row):
            if role == 'vision':
                self.vision_model.set(row.id)
                self.json_mode.set(next(label for label, value in JSON_MODES.items() if value == row.json_mode))
            elif role == 'reply':
                self.reply_model.set(row.id)
            self.protocol.set('Chat Completions（兼容接口）')
        def on_catalog(rows):
            if location is not None:
                self.catalogs[location] = rows
            self.fill_model_choices()
        return ModelBrowser(self, self.current_catalog(), role, self.base_url.get(), self.key.get(),
                            location, on_choose, on_catalog)

    def open_console(self):
        url = PRESETS[self.provider_id()]['console']
        if url:
            webbrowser.open(url)

    def save(self):
        try:
            provider = self.provider_id()
            profile = replace(self.store.profiles[provider], base_url=self.base_url.get(),
                              key=self.key.get(), protocol=PROTOCOLS[self.protocol.get()],
                              vision_model=self.vision_model.get(), reply_model=self.reply_model.get(),
                              json_mode=JSON_MODES[self.json_mode.get()])
            self.store.save(profile, typing_interval_ms=int(self.typing_interval.get()),
                            typing_corrections=self.typing_corrections.get())
        except (ValueError, OSError) as error:
            self.error.set('保存失败：' + str(error))
            return
        self.on_saved()
        self.destroy()
