"""Windows desktop entry point for V221 log review."""

import argparse
import csv
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from review_core import EXAMPLE, STATES, InputError, analyze, export_csv, load_policy, read_text


BG = '#f6f8fc'
SURFACE = '#eaf0fa'
INK = '#182133'
MUTED = '#4f5d73'
BLUE = '#2457ac'
COLORS = {'待复核': '#a74704', '证据不足': '#6850a4', '未触发': '#526984'}


def value_text(value):
    return '—' if value is None else f'{value:.4f}'


class ReviewWindow:
    def __init__(self, root, policy):
        self.root = root
        self.policy = policy
        self.runs = []
        self.visible = []
        root.title('假正常复核 V221')
        root.geometry('1070x720')
        root.minsize(820, 560)
        root.configure(background=BG)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), foreground=INK, background=BG)
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 20, 'bold'), foreground=INK)
        style.configure('Sub.TLabel', foreground=MUTED)
        style.configure('Card.TFrame', background=SURFACE)
        style.configure('Card.TLabel', background=SURFACE, foreground=MUTED)
        style.configure('Count.TLabel', background=SURFACE, font=('Microsoft YaHei UI', 19, 'bold'))
        style.configure('TButton', padding=(12, 7))
        style.configure('Primary.TButton', foreground='#ffffff', background=BLUE)
        style.map('Primary.TButton', background=[('active', '#184388')])
        style.configure('Treeview', rowheight=30, background='#ffffff', fieldbackground='#ffffff')
        style.configure('Treeview.Heading', background=SURFACE, padding=7)

        body = ttk.Frame(root, padding=20)
        body.pack(fill='both', expand=True)
        toolbar = ttk.Frame(body)
        toolbar.pack(fill='x')
        ttk.Label(toolbar, text='假正常复核', style='Title.TLabel').pack(side='left')
        ttk.Button(toolbar, text='打开日志', command=self.open_logs, style='Primary.TButton').pack(side='right')
        self.example_button = ttk.Button(toolbar, text='载入真实回放', command=self.open_example)
        self.example_button.pack(side='right', padx=8)
        if not EXAMPLE.is_file():
            self.example_button.state(['disabled'])
        self.source = tk.StringVar(value='V221  /  选择一份合并 CSV，或三份项目导出表')
        ttk.Label(body, textvariable=self.source, style='Sub.TLabel').pack(anchor='w', pady=(5, 15))

        filters = ttk.Frame(body)
        filters.pack(fill='x')
        ttk.Label(filters, text='运行').pack(side='left')
        self.run_pick = ttk.Combobox(filters, state='readonly', width=37)
        self.run_pick.pack(side='left', padx=(8, 16))
        self.run_pick.bind('<<ComboboxSelected>>', self.refresh)
        self.filter_pick = ttk.Combobox(filters, state='readonly', values=(*STATES, '全部'), width=9)
        self.filter_pick.set(STATES[0])
        self.filter_pick.pack(side='left')
        self.filter_pick.bind('<<ComboboxSelected>>', self.refresh)
        self.export = ttk.Button(filters, text='导出当前清单', command=self.save_list, state='disabled')
        self.export.pack(side='right')

        totals = ttk.Frame(body)
        totals.pack(fill='x', pady=(14, 7))
        self.counts = {}
        for column, state in enumerate(STATES):
            totals.columnconfigure(column, weight=1, uniform='state')
            card = ttk.Frame(totals, padding=(13, 7), style='Card.TFrame')
            card.grid(row=0, column=column, padx=(0, 8 if column < 2 else 0), sticky='ew')
            ttk.Label(card, text=f'{state}窗口', style='Card.TLabel').pack(anchor='w')
            self.counts[state] = tk.StringVar(value='—')
            ttk.Label(card, textvariable=self.counts[state], style='Count.TLabel', foreground=COLORS[state]).pack(anchor='w')

        self.timeline = tk.Canvas(body, height=20, background=BG, highlightthickness=0)
        self.timeline.pack(fill='x', pady=(2, 9))
        self.timeline.bind('<Configure>', self.draw_timeline)
        grid = ttk.Frame(body)
        grid.pack(fill='both', expand=True)
        cols = ('time', 'frames', 'state', 'reason')
        self.table = ttk.Treeview(grid, columns=cols, show='headings', selectmode='browse')
        for name, label, width, stretch in zip(cols, ('相对时间 (秒)', '帧号', '状态', '触发依据'),
                                                (155, 120, 95, 330), (False, False, False, True)):
            self.table.heading(name, text=label, anchor='w')
            self.table.column(name, width=width, stretch=stretch, anchor='w')
        self.table.pack(side='left', fill='both', expand=True)
        for state in STATES:
            self.table.tag_configure(state, foreground=COLORS[state])
        scroll = ttk.Scrollbar(grid, orient='vertical', command=self.table.yview)
        scroll.pack(side='right', fill='y')
        self.table.configure(yscrollcommand=scroll.set)
        self.table.bind('<<TreeviewSelect>>', self.show_detail)
        ttk.Label(body, text='人工复核提示。每个时刻对应一秒窗口的末端。', style='Sub.TLabel').pack(anchor='w', pady=(10, 3))
        self.detail = tk.StringVar(value='选中时段可查看原始时戳及分值。')
        ttk.Label(body, textvariable=self.detail, justify='left', style='Sub.TLabel').pack(anchor='w')
        root.bind('<Control-o>', lambda _: self.open_logs())

    def open_logs(self):
        paths = filedialog.askopenfilenames(title='选择 V221 日志', filetypes=[('CSV 日志', '*.csv')])
        if paths:
            self.load(paths)

    def open_example(self):
        self.load((EXAMPLE,), 'EuRoC V1_02 真实实验回放')

    def load(self, paths, name=None):
        try:
            runs = analyze([read_text(path) for path in paths], self.policy)
        except (InputError, OSError, UnicodeError, csv.Error) as error:
            messagebox.showerror('日志未导入', str(error), parent=self.root)
            return
        self.runs = runs
        labels = [f"{run['parent']}  /  {run['condition']}" for run in runs]
        self.run_pick['values'] = [f"{label}  /  {run['run']}" if labels.count(label) > 1 else label
                                   for label, run in zip(labels, runs, strict=True)]
        self.run_pick.current(0)
        self.source.set(f'V221  /  {name or Path(paths[0]).name}  /  {sum(r["total"] for r in runs):,} 个窗口')
        self.refresh()

    def active_run(self):
        return self.runs[self.run_pick.current()]

    def refresh(self, _event=None):
        run = self.active_run()
        for state, count in self.counts.items():
            count.set(f"{run['counts'].get(state, 0):,}")
        selected = self.filter_pick.get()
        self.visible = [item for item in run['segments'] if selected in ('全部', item['state'])]
        self.table.delete(*self.table.get_children())
        for index, item in enumerate(self.visible):
            self.table.insert('', 'end', iid=str(index), tags=(item['state'],), values=(
                f"{item['start']:.3f}  —  {item['end']:.3f}", f"{item['first']} – {item['last']}",
                item['state'], item['reason']))
        self.export.configure(state='normal' if self.visible else 'disabled')
        self.detail.set('选中时段可查看原始时戳及分值。' if self.visible else '当前筛选无时段。')
        self.draw_timeline()

    def draw_timeline(self, _event=None):
        self.timeline.delete('all')
        if not self.runs:
            return
        values = self.active_run()['segments']
        width = max(self.timeline.winfo_width(), 2)
        span = values[-1]['end'] or 1
        self.timeline.create_rectangle(0, 5, width, 15, fill=SURFACE, outline='')
        for item in values:
            left = (width - 2) * item['start'] / span
            right = (width - 2) * item['end'] / span
            self.timeline.create_rectangle(left, 5, max(left + 2, right), 15, fill=COLORS[item['state']], outline='')

    def show_detail(self, _event=None):
        chosen = self.table.selection()
        if chosen:
            item = self.visible[int(chosen[0])]
            self.detail.set(f"原始时戳 (ns)  {item['first_ns']}  →  {item['last_ns']}\n"
                            f"窗口 {item['count']} 个    八列峰值 {value_text(item['score'])}    "
                            f"双目单窗 {value_text(item['single'])}    连续三窗 {value_text(item['triple'])}")

    def save_list(self):
        path = filedialog.asksaveasfilename(title='导出复核清单', defaultextension='.csv',
                                            initialfile='V221复核清单.csv', filetypes=[('CSV 清单', '*.csv')])
        if path:
            try:
                Path(path).write_text(export_csv(self.active_run(), self.visible), encoding='utf-8', newline='')
            except OSError as error:
                messagebox.showerror('清单未保存', str(error), parent=self.root)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--example', action='store_true')
    args = parser.parse_args()
    root = tk.Tk()
    view = ReviewWindow(root, load_policy())
    if args.example:
        root.after(100, view.open_example)
    root.mainloop()


if __name__ == '__main__':
    main()
