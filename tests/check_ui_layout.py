"""量一下界面布局：每个设置页需要多高、日志区实际分到多高。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.app import App  # noqa: E402


def main() -> int:
    app = App()

    def probe() -> None:
        app.update_idletasks()
        print(f"窗口 {app.winfo_width()}x{app.winfo_height()}  DPI系数 {app._dpi:.2f}")
        for index in range(app.notebook.index("end")):
            app.notebook.select(index)
            app.update_idletasks()
            app._place_sash()
            app.update_idletasks()
            tab = app.notebook.nametowidget(app.notebook.tabs()[index])
            need = tab.content.winfo_reqheight() + 40
            got = app.notebook.winfo_height()
            state = "完全放得下" if need <= got else f"需滚动 {need - got}px"
            title = app.notebook.tab(index, "text")
            print(f"  {title:14} 需要 {need:4}px  分配 {got:4}px  {state}")
        total = app.paned.winfo_height()
        log_h = app.log_view.winfo_height()
        print(
            f"  日志区 {log_h}px（占主体 {log_h / max(total, 1) * 100:.0f}%），"
            f"字体 {app.log_view.cget('font')}"
        )
        app.destroy()

    app.after(700, probe)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
