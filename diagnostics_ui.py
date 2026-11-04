"""Local diagnostic viewer and explicit export; never uploads logs."""
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, filedialog
import diagnostics


class DiagnosticsDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title('诊断日志')
        self.geometry('850x540')
        body = ttk.Frame(self, padding=16)
        body.pack(fill='both', expand=True)
        self.note = tk.StringVar()
        location = diagnostics.folder()
        ttk.Label(body, text='本机日志目录：' + (str(location) if location else '未初始化'), wraplength=810).pack(anchor='w')
        ttk.Label(body, text='记录会话标题、坐标、校验原因和耗时；不含聊天/回复正文、密钥或截图。', wraplength=810).pack(anchor='w', pady=6)
        self.text = tk.Text(body, wrap='word', font=('Menlo', 10))
        self.text.pack(fill='both', expand=True)
        ttk.Label(body, textvariable=self.note, wraplength=810).pack(anchor='w', pady=6)
        row = ttk.Frame(body)
        row.pack(fill='x')
        ttk.Button(row, text='刷新', command=self.refresh).pack(side='left')
        ttk.Button(row, text='打开日志目录', command=self.open_folder).pack(side='left', padx=8)
        self.export_button = ttk.Button(row, text='导出诊断 ZIP', command=self.export)
        self.export_button.pack(side='right')
        ttk.Button(row, text='关闭', command=self.destroy).pack(side='right', padx=8)
        self.refresh()

    def refresh(self):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('end', diagnostics.tail())
        self.text.see('end')
        self.text.configure(state='disabled')

    def export(self):
        path = filedialog.asksaveasfilename(parent=self, title='导出本机诊断日志',
                                          initialfile='RpaWx-diagnostics.zip', defaultextension='.zip',
                                          filetypes=[('ZIP', '*.zip')])
        if not path:
            return
        try:
            diagnostics.export_bundle(path)
            self.note.set('已导出：' + path)
        except (OSError, ValueError):
            self.note.set('导出失败，请检查保存位置权限。')

    def open_folder(self):
        location = diagnostics.folder()
        if location is None:
            self.note.set('日志目录尚未初始化。')
            return
        try:
            if sys.platform == 'darwin':
                subprocess.Popen(['open', str(location)])
            elif sys.platform == 'win32':
                os.startfile(str(location))
        except OSError:
            self.note.set('无法打开日志目录，可复制上方路径手动打开。')
