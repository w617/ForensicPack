import sys
from gui_components.app import ForensicPackApp

def launch_gui() -> None:
    app = ForensicPackApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()

if __name__ == "__main__":
    launch_gui()
