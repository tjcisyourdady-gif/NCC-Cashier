"""更新检查与下载在后台线程运行，界面由主线程刷新。"""
import threading
import tkinter as tk
from tkinter import ttk


class UpdateDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app=app
        self.title('NCC 自动更新')
        self.geometry('660x430')
        self.minsize(550,360)
        self.transient(app.root)
        outer=ttk.Frame(self,padding=18)
        outer.pack(fill='both',expand=True)
        self.version=tk.StringVar()
        self.message=tk.StringVar()
        ttk.Label(outer,textvariable=self.version).pack(anchor='w')
        ttk.Label(outer,text='每次打开程序自动检查；优先国内直连通道。',style='Hint.TLabel').pack(anchor='w',pady=6)
        ttk.Label(outer,textvariable=self.message,wraplength=590).pack(fill='x',pady=8)
        self.progress=ttk.Progressbar(outer,maximum=100)
        self.progress.pack(fill='x')
        self.notes=tk.Text(outer,height=9,wrap='word',state='disabled')
        self.notes.pack(fill='both',expand=True,pady=12)
        buttons=ttk.Frame(outer)
        buttons.pack(fill='x')
        self.check_button=ttk.Button(buttons,text='重新检查',command=app.check_update)
        self.check_button.pack(side='left')
        self.download_button=ttk.Button(buttons,text='下载完整更新',command=self.download)
        self.download_button.pack(side='left',padx=8)
        self.install_button=ttk.Button(buttons,text='安装并重启',command=app.install_update)
        self.install_button.pack(side='left')
        ttk.Button(buttons,text='关闭',command=self.destroy).pack(side='right')
        ttk.Label(outer,text='安装前请停止任务。已有设置、登录数据、结算记录和清单结果会保留。',
            wraplength=610,style='Hint.TLabel').pack(fill='x',pady=(10,0))
        self.last_notes=None
        self.timer=None
        self.refresh()

    def download(self):
        threading.Thread(target=self.app.updater.download,name='NCCUpdateDownload',daemon=True).start()

    def refresh(self):
        data=self.app.updater.status()
        self.version.set('当前版本：{}    检测到的版本：{}'.format(data['current_version'],data['latest_version'] or '—'))
        self.message.set(data['message']+('（'+data['source']+'）' if data.get('source') else ''))
        self.progress['value']=data['progress']
        if data['notes']!=self.last_notes:
            self.notes.configure(state='normal')
            self.notes.delete('1.0','end')
            self.notes.insert('end',data['notes'] or '暂无新版本说明。')
            self.notes.configure(state='disabled')
            self.last_notes=data['notes']
        busy=data['status'] in ('checking','downloading','staging','installing')
        self.check_button.configure(state='disabled' if busy else 'normal')
        self.download_button.configure(state='normal' if data['status']=='available' else 'disabled')
        self.install_button.configure(state='normal' if data['status']=='ready' and not self.app.task_running() else 'disabled')
        self.timer=self.after(300,self.refresh)

    def destroy(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer=None
        super().destroy()
