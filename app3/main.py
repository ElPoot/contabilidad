from __future__ import annotations

import sys
from pathlib import Path

# Agregar la raiz del repo a sys.path para que 'app3' sea encontrado
# sin importar desde donde se ejecute el script.
_ROOT = Path(__file__).resolve().parent.parent  # sube de app3/ a contabilidad/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app3.bootstrap import bootstrap_legacy_paths  # noqa: E402

bootstrap_legacy_paths()

from app3.gui.main_window import App3Window  # noqa: E402


def main() -> None:
    app = App3Window()
    app.mainloop()


if __name__ == "__main__":
    main()
