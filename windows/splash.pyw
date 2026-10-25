"""Startup window shown while the engine loads; a separate process so Tk owns its main thread.

Closes itself once the engine answers (after a short 'opening' message), after 4 minutes
at most, or when the launcher terminates it.
"""
import json
import tkinter as tk
import urllib.request

URL = 'http://127.0.0.1:8877/api/state'
root = tk.Tk()
root.title('Same Page')
root.overrideredirect(True)
root.configure(bg='#f6f4ef')
w, h = 360, 150
root.geometry(f'{w}x{h}+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 2}')
root.attributes('-topmost', True)
tk.Label(root, text='Same Page', bg='#f6f4ef', fg='#173d34', font=('Microsoft YaHei UI', 18, 'bold')).pack(pady=(30, 6))
text = tk.StringVar(value='正在启动本地引擎…第一次打开可能需要半分钟')
tk.Label(root, textvariable=text, bg='#f6f4ef', fg='#5d6662', font=('Microsoft YaHei UI', 10)).pack()
waited = 0


def check():
    global waited
    waited += 1
    try:
        with urllib.request.urlopen(URL, timeout=1) as r:
            if 'token' in json.load(r):
                text.set('正在打开窗口…')
                root.after(2500, root.destroy)
                return
    except Exception:
        pass
    if waited > 240:
        root.destroy()
        return
    root.after(1000, check)


root.after(500, check)
root.mainloop()
