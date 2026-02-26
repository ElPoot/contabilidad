from pathlib import Path
import logging

from facturacion_system.core.settings import get_setting

NETWORK_DRIVE = Path(str(get_setting("network_drive", "Z:/DATA")))
FISCAL_YEAR = int(get_setting("fiscal_year"))
PF_DIR = NETWORK_DRIVE / f"PF-{FISCAL_YEAR}"
CLIENTS_DIR = PF_DIR / "CLIENTES"
SIN_CLASIFICAR_DIR = PF_DIR / "SIN_CLASIFICAR"

if not NETWORK_DRIVE.exists():
    logging.warning(
        "Unidad de red no disponible (%s). Inicia RaiDrive y monta OneDrive empresarial.",
        NETWORK_DRIVE,
    )

HACIENDA_DIR = NETWORK_DRIVE / "HACIENDA"
CONFIG_DIR = NETWORK_DRIVE / "CONFIG"
DATABASE_DIR = NETWORK_DRIVE / "DATABASE"
LOGS_DIR = NETWORK_DRIVE / "LOGS"

CREDENTIALS_FILE = str(HACIENDA_DIR / "credentials.json")
TOKENS_DIR = str(HACIENDA_DIR / "tokens")
CURRENT_ACCOUNT_FILE = str(HACIENDA_DIR / "tokens" / ".current_account")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def initialize_directory_structure():
    """Crea carpetas críticas si no existen."""
    if not NETWORK_DRIVE.exists():
        return
    for dir_path in [
        HACIENDA_DIR,
        CONFIG_DIR,
        DATABASE_DIR,
        LOGS_DIR,
        CLIENTS_DIR,
        SIN_CLASIFICAR_DIR,
    ]:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            logging.warning("No se pudo crear carpeta: %s", dir_path, exc_info=True)


initialize_directory_structure()

APP_LOG_FILE = LOGS_DIR / "app.log"
if not logging.getLogger().handlers:
    handlers = []
    if NETWORK_DRIVE.exists():
        try:
            handlers.append(logging.FileHandler(APP_LOG_FILE, encoding="utf-8"))
        except OSError:
            pass
    if not handlers:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )
