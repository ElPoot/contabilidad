from pathlib import Path

import customtkinter as ctk

from facturacion_system.core.settings import get_setting

# Importamos la vista desde su NUEVA ubicación en gui (ya no gui2)
from facturacion_system.gui.downloader_view import SmartDownloaderView


class MyApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode(get_setting("appearance_mode", "System"))
        ctk.set_default_color_theme("blue")
        self.title("MASS-DOWNLOAD - Gmail Downloader")
        try:
            icon_candidates = [
                Path(__file__).resolve().parent.parent / "assets" / "MassDownload.ico",
                Path(__file__).resolve().parent / "assets" / "MassDownload.ico",
            ]
            for icon_path in icon_candidates:
                if icon_path.exists():
                    self.iconbitmap(str(icon_path))
                    break
        except Exception:
            pass
        self.geometry("1920x1080")

        self.view = SmartDownloaderView(self)
        self.view.pack(fill="both", expand=True, padx=10, pady=10)


if __name__ == "__main__":
    app = MyApp()
    app.mainloop()
