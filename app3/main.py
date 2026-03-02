from __future__ import annotations

import logging
import sys
from pathlib import Path

# ── Configurar logging (para diagnóstico) ──────────────────────────────────────
log_file = Path.home() / ".app3_logs" / "app3.log"
log_file.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,  # DEBUG para más detalles
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(log_file),  # A archivo
        logging.StreamHandler(),  # También a consola si existe
    ]
)
logger = logging.getLogger(__name__)
logger.info(f"Logs se escriben en: {log_file}")

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
from app3.gui.main_window import App3Window         # noqa: E402


def main() -> None:
    logger.info("Iniciando App3...")
    app = App3Window()
    app.mainloop()


if __name__ == "__main__":
    main()
