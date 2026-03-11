from __future__ import annotations

import logging
import sys
from pathlib import Path

# ── Configurar logging (para diagnóstico) ──────────────────────────────────────
log_file = Path.home() / ".gestor_contable_logs" / "gestor_contable.log"
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
#   python gestor_contable/main.py          (desde contabilidad/)
#   python -m gestor_contable.main          (desde contabilidad/)
#   python c:/.../gestor_contable/main.py   (ruta absoluta)
_HERE = Path(__file__).resolve().parent   # = .../contabilidad/gestor_contable/
_ROOT = _HERE.parent                      # = .../contabilidad/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Ahora 'gestor_contable' es un paquete visible
from gestor_contable.gui.main_window import App3Window         # noqa: E402


def main() -> None:
    logger.info("Iniciando Gestor Contable...")
    app = App3Window()
    app.mainloop()


if __name__ == "__main__":
    main()
