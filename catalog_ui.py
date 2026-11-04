"""Searchable model directory with an explicit, cancellable metadata sync."""
import queue
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser

from model_catalog import (CATALOG_DATE, MODELS_DOC, ROLE_LABELS, fetch_catalog,
                           filter_catalog, save_cache)


class ModelBrowser(tk.Toplevel):
    def __init__(self, master, rows, role, base_url, key, path, on_choose, on_catalog):
        super().__init__(master)
        self.parent, self.rows, self.role = master, rows, role
        self.base_url, self.key, self.cache_file = base_url, key, path
        self.on_choose, self.on_catalog = on_choose, on_catalog
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.syncing = False
        self.matches = ()
        self.title('百炼模型目录' + (' · ' + ROLE_LABELS[role] if role else ' · 全部类别'))
        self.geometry('980x640')
        self.minsize(820, 540)
        self.transient(master)
        self.query = tk.StringVar()
        self.category = tk.StringVar(value='全部')
        self.show_all = tk.BooleanVar(value=role is None)
        self.summary = tk.StringVar()
        self.detail = tk.StringVar(value='选择模型查看适用任务；地域、开通状态和免费额度以百炼控制台为准。')
        self.status = tk.StringVar(value=f'内置文档快照：{CATALOG_DATE}；可同步当前接入点的官方目录。')
        body = ttk.Frame(self, padding=18)
        body.pack(fill='both', expand=True)
        filters = ttk.Frame(body)
        filters.pack(fill='x', pady=(0, 8))
        ttk.Label(filters, text='搜索模型').pack(side='left')
        search = ttk.Entry(filters, textvariable=self.query)
        search.pack(side='left', fill='x', expand=True, padx=8)
        self.category_entry = ttk.Combobox(filters, textvariable=self.category, state='readonly', width=18)
        self.category_entry.pack(side='left')
        self.category_entry.bind('<<ComboboxSelected>>', lambda event: self.refresh())
        ttk.Checkbutton(body, text='查看全部类别（包含不能用于当前任务的模型）',
                        variable=self.show_all, command=self.refresh).pack(anchor='w')
        ttk.Label(body, textvariable=self.summary).pack(anchor='w', pady=6)
        table = ttk.Frame(body)
        table.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(table, columns=('id', 'category', 'roles', 'origin'),
                                 show='headings', selectmode='browse', height=14)
        for column, title, width in [('id', '模型 ID', 490), ('category', '类别', 120),
                                      ('roles', '本程序可用任务', 160), ('origin', '来源', 110)]:
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=80, stretch=column == 'id')
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.bind('<<TreeviewSelect>>', self.selection_changed)
        self.tree.bind('<Double-1>', lambda event: self.choose())
        ttk.Label(body, textvariable=self.detail, wraplength=910, foreground='#625e72').pack(anchor='w', pady=(10, 4))
        ttk.Label(body, textvariable=self.status, wraplength=910, foreground='#625e72').pack(anchor='w', pady=(0, 8))
        actions = ttk.Frame(body)
        actions.pack(fill='x')
        self.sync_button = ttk.Button(actions, text='同步官方模型目录', command=self.sync)
        self.sync_button.pack(side='left')
        ttk.Button(actions, text='官方模型文档', command=lambda: webbrowser.open(MODELS_DOC)).pack(side='left', padx=8)
        self.choose_button = ttk.Button(actions, text='用于' + ROLE_LABELS[role] if role else '仅浏览目录',
                                        state='disabled', command=self.choose)
        self.choose_button.pack(side='right')
        ttk.Button(actions, text='关闭', command=self.close).pack(side='right', padx=8)
        self.query.trace_add('write', lambda *args: self.refresh())
        self.update_categories()
        self.refresh()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda event: self.close())
        self.timer = self.after(100, self.drain)
        self.grab_set()
        search.focus_set()

    def update_categories(self):
        values = ['全部'] + sorted({row.category for row in self.rows})
        self.category_entry.configure(values=values)
        if self.category.get() not in values:
            self.category.set('全部')

    def refresh(self):
        self.matches = filter_catalog(self.rows, self.query.get(), self.category.get(),
                                      None if self.show_all.get() else self.role)
        self.tree.delete(*self.tree.get_children())
        for index, row in enumerate(self.matches):
            tasks = '、'.join(ROLE_LABELS[role] for role in row.roles) or '仅目录'
            self.tree.insert('', 'end', iid=str(index), values=(row.id, row.category, tasks, row.origin))
        self.summary.set(f'目录共 {len(self.rows)} 个模型；当前显示 {len(self.matches)} 个。可输入名称、版本或类别搜索。')
        self.choose_button.configure(state='disabled')

    def selected(self):
        selection = self.tree.selection()
        return self.matches[int(selection[0])] if selection else None

    def selection_changed(self, event=None):
        row = self.selected()
        if row is None:
            return
        usable = '、'.join(ROLE_LABELS[role] for role in row.roles) or '当前仅收录目录'
        text = row.id + ' · ' + row.category + ' · ' + usable
        if row.note:
            text += '\n' + row.note
        text += '\n目录不代表账号已开通或有免费额度；实际可用性以当前地域的百炼控制台为准。'
        self.detail.set(text)
        self.choose_button.configure(state='normal' if self.role in row.roles else 'disabled')

    def choose(self):
        row = self.selected()
        if row is not None and self.role in row.roles:
            self.on_choose(row)
            self.close()

    def sync(self):
        if self.syncing:
            return
        if not self.key.strip():
            self.status.set('请先关闭目录，在设置中填写当前地域的百炼 API Key，再打开目录同步。')
            return
        if self.cache_file is None:
            self.status.set('当前 Base URL 无效，请在设置中填写 /compatible-mode/v1 接入地址。')
            return
        self.syncing = True
        self.sync_button.configure(state='disabled')
        self.status.set('正在逐页查询官方模型目录，可关闭窗口取消；不发送微信截图。')
        def work():
            try:
                rows = fetch_catalog(self.base_url, self.key, self.cancel)
                if not self.cancel.is_set():
                    save_cache(self.cache_file, rows)
                    self.events.put(('success', rows))
            except Exception as error:
                self.events.put(('error', str(error)))
        threading.Thread(target=work, daemon=True).start()

    def drain(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                self.syncing = False
                self.sync_button.configure(state='normal')
                if kind == 'success':
                    self.rows = value
                    self.on_catalog(value)
                    self.update_categories()
                    self.refresh()
                    self.status.set(f'已同步并缓存当前接入点的 {len(value)} 个模型；目录不代表账户已获所有模型调用权限。')
                else:
                    self.status.set('同步失败，保留原目录。' + value)
        except queue.Empty:
            pass
        self.timer = self.after(100, self.drain)

    def close(self):
        self.cancel.set()
        self.after_cancel(self.timer)
        self.destroy()
        if self.parent.winfo_exists():
            self.parent.grab_set()
