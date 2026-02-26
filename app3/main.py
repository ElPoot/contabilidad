from __future__ import annotations

import sys
from pathlib import Path

# ── Asegurar que la raíz del repo esté en sys.path ───────────────────────────
# Funciona tanto con:
#   python app3/main.py          (desde contabilidad/)
#   python -m app3.main          (desde contabilidad/)
#   python c:/.../app3/main.py   (ruta absoluta)
_HERE = Path(__file__).resolve().parent   # = .../contabilidad/app3/
_ROOT = _HERE.parent                      # = .../contabilidad/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Ahora 'app3' es un paquete visible
from app3.bootstrap import bootstrap_legacy_paths   # noqa: E402
bootstrap_legacy_paths()

from app3.gui.main_window import App3Window         # noqa: E402


def main() -> None:
    app = App3Window()
    app.mainloop()


if __name__ == "__main__":
    main()
