from __future__ import annotations

from app3.bootstrap import bootstrap_legacy_paths

bootstrap_legacy_paths()

from app3.gui.main_window import App3Window  # noqa: E402


def main() -> None:
    app = App3Window()
    app.mainloop()


if __name__ == "__main__":
    main()
