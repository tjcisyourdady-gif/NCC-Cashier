"""更新检查与下载在后台线程运行，界面由主线程刷新。"""
import threading
import tkinter as tk
from tkinter import ttk


class StartupUpdatePrompt(tk.Toplevel):
    """只在本程序前台时提示一次；关闭按钮等同于忽略本次。"""
    def __init__(self,app,data):
        super().__init__(app.root)
        self.app=app
        self.title('发现新版本')
        self.geometry('560x350')
        self.minsize(480,300)
        self.transient(app.root)
        self.protocol('WM_DELETE_WINDOW',self.ignore)
        area=ttk.Frame(self,padding=18)
        area.pack(fill='both',expand=True)
        ttk.Label(area,text='发现新版本 V'+data['latest_version'],font=('Microsoft YaHei UI',14,'bold')).pack(anchor='w')
        ttk.Label(area,text='当前版本：V'+data['current_version']).pack(anchor='w',pady=(6,10))
        notes=tk.Text(area,height=7,wrap='word')
        notes.insert('1.0',data.get('notes') or '有新的程序版本可用。')
        notes.configure(state='disabled')
        notes.pack(fill='both',expand=True)
        ttk.Label(area,text='更新后将自动重启，保留已有设置和记录。忽略仅对本次启动生效。',
            wraplength=510,style='Hint.TLabel').pack(fill='x',pady=10)
        buttons=ttk.Frame(area)
        buttons.pack(fill='x')
        self.ignore_button=ttk.Button(buttons,text='忽略',command=self.ignore)
        self.ignore_button.pack(side='right')
        self.update_button=ttk.Button(buttons,text='更新',command=self.update_now)
        self.update_button.pack(side='right',padx=8)
        self.update_idletasks()
        if self.winfo_viewable():
            self.grab_set()
            self.update_button.focus_set()

    def ignore(self):
        self.app._startup_prompt_handled=True
        self.app._startup_pending_update=None
        self.destroy()

    def update_now(self):
        self.destroy()
        self.app.start_prompted_update()

    def destroy(self):
        if getattr(self.app,'startup_update_dialog',None) is self:
            self.app.startup_update_dialog=None
        super().destroy()


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
        self.download_button=ttk.Button(buttons,text='下载更新',command=self.download)
        self.download_button.pack(side='left',padx=8)
        self.install_button=ttk.Button(buttons,text='安装并重启',command=app.install_update)
        self.install_button.pack(side='left')
        ttk.Button(buttons,text='关闭',command=self.destroy).pack(side='right')
        self.flow_hint=tk.StringVar()
        ttk.Label(outer,textvariable=self.flow_hint,wraplength=610,style='Hint.TLabel').pack(fill='x',pady=(10,0))
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
        busy=self.app._update_installing or data['status'] in ('checking','downloading','staging','starting','installing')
        self.flow_hint.set('下载完成后将自动安装并重启，请稍候。设置和记录会保留。' if self.app._auto_update_requested else
            '安装前请停止任务。已有设置、登录数据、结算记录和清单结果会保留。')
        self.check_button.configure(state='disabled' if busy else 'normal')
        self.download_button.configure(state='normal' if data['status']=='available' and not busy else 'disabled')
        self.install_button.configure(state='normal' if data['status']=='ready' and not busy and not self.app.task_running() else 'disabled')
        self.timer=self.after(300,self.refresh)

    def destroy(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer=None
        super().destroy()
