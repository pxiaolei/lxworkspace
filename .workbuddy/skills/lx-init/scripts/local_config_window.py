"""Collect credentials in a local window; never print or persist the inputs."""


class ConfigForm:
    def __init__(self, window, root, fields, ttk):
        self.window = window
        self.fields = fields
        self.result = None
        self.entries = {}
        window.title("lx 工作区 · 本机配置")
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=24)
        frame.grid()
        ttk.Label(frame, text="填写本机配置", font=("", 16)).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="只保存在当前电脑，不发送到聊天。已有配置保持原样。").grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 4))
        ttk.Label(frame, text=str(root), wraplength=560).grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 14))
        for row, field in enumerate(fields, 3):
            ttk.Label(frame, text=field["label"]).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=7)
            entry = ttk.Entry(frame, width=42, show="*" if field["secret"] else "")
            entry.grid(row=row, column=1, sticky="ew", pady=7)
            self.entries[field["name"]] = entry
            if field["status"] == "kept":
                entry.configure(state="disabled")
            ttk.Label(frame, text="已配置，保留" if field["status"] == "kept" else "待填写").grid(row=row, column=2, padx=(10, 0))
        row = len(fields) + 3
        self.message = ttk.Label(frame, text="密码、Key、token 默认隐藏。", wraplength=560)
        self.message.grid(row=row, column=0, columnspan=3, sticky="w", pady=12)
        buttons = ttk.Frame(frame)
        buttons.grid(row=row+1, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="取消", command=self.cancel).grid(row=0, column=0, padx=8)
        ttk.Button(buttons, text="保存到本机", command=self.save).grid(row=0, column=1)
        window.protocol("WM_DELETE_WINDOW", self.cancel)
        window.bind("<Escape>", lambda _event: self.cancel())
        # Tk normally prints callback exceptions, which may contain user input.
        window.report_callback_exception = lambda *_args: self.message.configure(text="窗口操作失败，请取消并联系对接人。")
        for field in fields:
            if field["status"] != "kept":
                self.entries[field["name"]].focus_set()
                break

    def save(self):
        values = {name: entry.get() for name, entry in self.entries.items()}
        missing = [f["label"] for f in self.fields if f["status"] != "kept" and not values[f["name"]].strip()]
        if missing:
            self.message.configure(text="请填写：" + "、".join(missing))
            return
        if any(any(c in value for c in ("\n", "\r", "\x00")) for value in values.values()):
            self.message.configure(text="输入不能包含换行或控制字符，请检查后再保存。")
            return
        self.result = values
        self.window.destroy()

    def cancel(self):
        self.result = None
        self.window.destroy()


def collect_values(root, fields):
    # Lazy imports keep checks and file-import compatibility usable without Tk.
    import tkinter as tk
    from tkinter import ttk

    window = tk.Tk()
    try:
        form = ConfigForm(window, root, fields, ttk)
        window.lift()
        window.mainloop()
        return form.result
    finally:
        try:
            window.destroy()
        except tk.TclError:
            pass
