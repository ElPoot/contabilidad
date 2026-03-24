from __future__ import annotations

import logging
import sys
from pathlib import Path

# ── Configurar logging ─────────────────────────────────────────────────────────
log_file = Path.home() / ".gestor_contable_logs" / "gestor_contable.log"
log_file.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)
logger.info("Logs se escriben en: %s", log_file)

# ── Asegurar que la raíz del repo esté en sys.path ───────────────────────────
_HERE = Path(__file__).resolve().parent   # = .../contabilidad/gestor_contable/
_ROOT = _HERE.parent                      # = .../contabilidad/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from gestor_contable.config import ensure_drive_mounted        # noqa: E402
from gestor_contable.gui.main_window import App3Window         # noqa: E402


def _run_setup_if_needed() -> bool:
    """
    Muestra la ventana de configuración inicial si Z: no se puede montar.
    Retorna True si al terminar Z: está disponible, False si el usuario cerró
    sin configurar.
    """
    # Primer intento: montar sin intervención del usuario
    if ensure_drive_mounted():
        return True

    logger.warning("No se pudo montar Z: automáticamente — mostrando configuración inicial.")

    from gestor_contable.gui.setup_window import SetupWindow

    reason = "No se encontró la carpeta de OneDrive. Configura la ruta para continuar."
    setup = SetupWindow(reason=reason)
    setup.mainloop()

    if not setup._completed:
        logger.info("El usuario cerró la configuración sin guardar.")
        return False

    # Segundo intento tras la configuración
    if ensure_drive_mounted():
        return True

    logger.error("Aún no se puede montar Z: después de la configuración.")
    return False


def main() -> None:
    logger.info("Iniciando Gestor Contable...")

    drive_ok = _run_setup_if_needed()
    if not drive_ok:
        # La app puede iniciar igual (mostrará errores al cargar clientes),
        # pero al menos no falla en blanco. El usuario verá el mensaje
        # correspondiente en la pantalla de login.
        logger.warning("Iniciando app sin disco Z: — funcionalidad limitada.")

    app = App3Window()
    app.mainloop()


if __name__ == "__main__":
    main()
