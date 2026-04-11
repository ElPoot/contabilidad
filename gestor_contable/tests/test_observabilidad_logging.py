from __future__ import annotations

import contextlib
import shutil
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

import gestor_contable.config as config_module
import gestor_contable.core.classifier as classifier_module
from gestor_contable.core.atv_client import query_invoice_status
from gestor_contable.app.selection_controller import build_multi_vm, build_single_vm
from gestor_contable.core.catalog import CatalogManager
from gestor_contable.core.classifier import build_dest_folder, invalid_fecha_emision_message
from gestor_contable.core.classification_utils import (
    find_duplicate_pdfs_within_origin,
    find_duplicate_xmls_in_origin,
)
from gestor_contable.core.factura_index import FacturaIndexer
from gestor_contable.core.models import FacturaRecord
from gestor_contable.gui.main_window import App3Window
from gestor_contable.gui.setup_window import SetupWindow


class ObservabilidadLoggingTests(unittest.TestCase):
    @contextlib.contextmanager
    def _workspace_tempdir(self):
        base = Path.cwd() / "gestor_contable" / "data" / "_tmp_test_observabilidad"
        base.mkdir(parents=True, exist_ok=True)
        path = base / uuid4().hex
        path.mkdir()
        try:
            yield str(path)
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def test_catalog_manager_logs_invalid_catalog(self) -> None:
        with self._workspace_tempdir() as tmp:
            tmp_path = Path(tmp)
            catalog_path = tmp_path / "catalogo_cuentas.json"
            catalog_path.write_text("{ invalido", encoding="utf-8")

            with mock.patch.object(CatalogManager, "save", autospec=True, return_value=None):
                with self.assertLogs("gestor_contable.core.catalog", level="ERROR") as logs:
                    manager = CatalogManager(tmp_path).load()

            self.assertIsNotNone(manager._data)
            self.assertIn("Catálogo inválido", "\n".join(logs.output))

    def test_find_duplicate_xmls_logs_cache_read_failure(self) -> None:
        with self._workspace_tempdir() as tmp:
            client_folder = Path(tmp) / "cliente"
            xml_folder = client_folder / "XML"
            xml_folder.mkdir(parents=True)
            contenido = "<Factura>igual</Factura>"
            (xml_folder / "a.xml").write_text(contenido, encoding="utf-8")
            (xml_folder / "b.xml").write_text(contenido, encoding="utf-8")

            class BrokenXMLCache:
                def __init__(self, cache_file: Path, xml_root: Path) -> None:
                    self.xml_root = xml_root

                def load_all(self) -> dict[str, tuple[float, int, str]]:
                    return {
                        path.name: (path.stat().st_mtime, path.stat().st_size, "{ invalido")
                        for path in self.xml_root.rglob("*.xml")
                    }

                def _make_key(self, xml_path: Path) -> str:
                    return xml_path.name

                def close(self) -> None:
                    return None

            with mock.patch("gestor_contable.core.xml_cache.XMLCacheManager", BrokenXMLCache):
                with self.assertLogs("gestor_contable.core.classification_utils", level="WARNING") as logs:
                    duplicados = find_duplicate_xmls_in_origin(client_folder)

            joined = "\n".join(logs.output)
            self.assertEqual(1, len(duplicados))
            self.assertIn("No se pudo reutilizar caché XML", joined)
            self.assertIn("Escaneo duplicados XML:", joined)

    def test_find_duplicate_pdfs_logs_cache_read_failure(self) -> None:
        with self._workspace_tempdir() as tmp:
            client_folder = Path(tmp) / "cliente"
            pdf_folder = client_folder / "PDF"
            pdf_folder.mkdir(parents=True)
            contenido = b"%PDF-1.4 contenido igual"
            (pdf_folder / "a.pdf").write_bytes(contenido)
            (pdf_folder / "b.pdf").write_bytes(contenido)

            class BrokenPDFCache:
                def __init__(self, cache_file: Path, pdf_root: Path) -> None:
                    return None

                def _get_entry(self, pdf_path: Path) -> dict:
                    raise RuntimeError("cache PDF corrupto")

            with mock.patch("gestor_contable.core.pdf_cache.PDFCacheManager", BrokenPDFCache):
                with self.assertLogs("gestor_contable.core.classification_utils", level="WARNING") as logs:
                    duplicados = find_duplicate_pdfs_within_origin(client_folder)

            joined = "\n".join(logs.output)
            self.assertEqual(1, len(duplicados))
            self.assertIn("No se pudo reutilizar caché PDF", joined)
            self.assertIn("Escaneo duplicados PDF origen:", joined)

    def test_setup_window_save_logs_invalid_local_settings(self) -> None:
        from gestor_contable.gui import setup_window as setup_window_module

        with self._workspace_tempdir() as tmp:
            tmp_path = Path(tmp)
            settings_path = tmp_path / "local_settings.json"
            settings_path.write_text("{ invalido", encoding="utf-8")
            selected_dir = tmp_path / "OneDrive"
            selected_dir.mkdir()

            class DummyEntry:
                def __init__(self, value: str) -> None:
                    self._value = value

                def get(self) -> str:
                    return self._value

            class DummySetup:
                def __init__(self, value: str) -> None:
                    self._path_entry = DummyEntry(value)
                    self._completed = False

                def _set_feedback(self, text: str, color: str) -> None:
                    raise AssertionError(f"No se esperaba feedback de error: {text} [{color}]")

                def destroy(self) -> None:
                    return None

            dummy = DummySetup(str(selected_dir))
            with mock.patch.object(setup_window_module, "_LOCAL_SETTINGS", settings_path):
                with self.assertLogs("gestor_contable.gui.setup_window", level="WARNING") as logs:
                    SetupWindow._save(dummy)

            self.assertTrue(dummy._completed)
            self.assertIn('"subst_source"', settings_path.read_text(encoding="utf-8"))
            self.assertIn("No se pudo leer", "\n".join(logs.output))

    def test_query_invoice_status_logs_unreadable_error_body(self) -> None:
        class FakeResponse:
            status_code = 500
            ok = False
            headers = {"X-Error-Cause": "fallo-prueba"}

            @property
            def text(self) -> str:
                raise RuntimeError("body ilegible")

        with mock.patch("gestor_contable.core.atv_client._fetch_token", return_value="token-prueba"):
            with mock.patch("gestor_contable.core.atv_client.requests.get", return_value=FakeResponse()):
                with self.assertLogs("gestor_contable.core.atv_client", level="WARNING") as logs:
                    result = query_invoice_status("50612345678901234567890123456789012345678901234567")

        joined = "\n".join(logs.output)
        self.assertEqual("ATV respondio HTTP 500", result["error"])
        self.assertIn("No se pudo leer body de error ATV", joined)
        self.assertIn("<body no legible: RuntimeError: body ilegible>", joined)

    def test_build_dest_folder_logs_invalid_fecha_emision(self) -> None:
        session_folder = Path("Z:/DATA/PF-2026/CLIENTES/CLIENTE TEST")
        classifier_module._INVALID_FECHA_WARNED_VALUES.clear()

        with self.assertLogs("gestor_contable.core.classifier", level="WARNING") as logs:
            with self.assertRaisesRegex(RuntimeError, "fecha de emisión"):
                build_dest_folder(
                    session_folder,
                    "fecha-invalida",
                    "COMPRAS",
                    "",
                    "",
                    "Proveedor Demo",
                )

        self.assertIn("Fecha de emision invalida", "\n".join(logs.output))

    def test_build_dest_folder_logs_invalid_fecha_only_once_per_valor(self) -> None:
        session_folder = Path("Z:/DATA/PF-2026/CLIENTES/CLIENTE TEST")
        classifier_module._INVALID_FECHA_WARNED_VALUES.clear()

        with self.assertLogs("gestor_contable.core.classifier", level="WARNING") as logs:
            with self.assertRaises(RuntimeError):
                build_dest_folder(session_folder, "fecha-invalida", "COMPRAS", "", "", "Proveedor Demo")
            with self.assertRaises(RuntimeError):
                build_dest_folder(session_folder, "fecha-invalida", "COMPRAS", "", "", "Proveedor Demo")

        self.assertEqual(
            1,
            sum("Fecha de emision invalida" in line for line in logs.output),
        )

    def test_build_dest_folder_logs_expected_sentinel_as_debug(self) -> None:
        session_folder = Path("Z:/DATA/PF-2026/CLIENTES/CLIENTE TEST")
        classifier_module._INVALID_FECHA_WARNED_VALUES.clear()

        with self.assertLogs("gestor_contable.core.classifier", level="DEBUG") as logs:
            with self.assertRaises(RuntimeError):
                build_dest_folder(session_folder, "", "COMPRAS", "", "", "Proveedor Demo")

        joined = "\n".join(logs.output)
        self.assertIn("Fecha de emision no util", joined)
        self.assertNotIn("WARNING", joined)

    def test_build_dest_folder_logs_oserror_al_buscar_carpeta_existente(self) -> None:
        session_folder = Path("Z:/DATA/PF-2026/CLIENTES/CLIENTE TEST")

        with mock.patch("pathlib.Path.exists", return_value=False):
            with mock.patch("pathlib.Path.iterdir", side_effect=OSError("red no disponible")):
                with self.assertLogs("gestor_contable.core.classifier", level="WARNING") as logs:
                    dest = build_dest_folder(
                        session_folder,
                        "11/04/2026",
                        "COMPRAS",
                        "",
                        "",
                        "Proveedor Demo",
                    )

        self.assertIn("No se pudo leer", "\n".join(logs.output))
        self.assertIn("CLIENTE TEST", str(dest))

    def test_config_find_onedrive_path_logs_scan_failure(self) -> None:
        fake_home = Path("C:/Usuarios/Demo")

        with mock.patch.object(config_module, "_LOCAL_SETTINGS", fake_home / ".gestor_contable" / "local_settings.json"):
            with mock.patch.dict("os.environ", {}, clear=True):
                with mock.patch("pathlib.Path.home", return_value=fake_home):
                    with mock.patch("pathlib.Path.iterdir", side_effect=PermissionError("sin acceso")):
                        with self.assertLogs("gestor_contable.config", level="DEBUG") as logs:
                            result = config_module._find_onedrive_path()

        self.assertIsNone(result)
        self.assertIn("No se pudo escanear", "\n".join(logs.output))

    def test_config_is_onedrive_placeholder_logs_ctypes_failure(self) -> None:
        with mock.patch("ctypes.windll.kernel32.GetFileAttributesW", side_effect=RuntimeError("fallo ctypes")):
            with self.assertLogs("gestor_contable.config", level="DEBUG") as logs:
                result = config_module.is_onedrive_placeholder(Path("C:/demo.pdf"))

        self.assertFalse(result)
        self.assertIn("fallo consultando atributos Win32", "\n".join(logs.output))

    def test_main_window_get_mes_str_logs_invalid_fecha(self) -> None:
        with self.assertLogs("gestor_contable.gui.main_window", level="DEBUG") as logs:
            result = App3Window._get_mes_str("fecha-invalida")

        self.assertEqual("", result)
        self.assertIn("No se pudo convertir fecha", "\n".join(logs.output))

    def test_invalid_fecha_emision_message_for_empty_value(self) -> None:
        self.assertIn("no tiene fecha de emisión válida", invalid_fecha_emision_message(""))

    def test_build_single_vm_blocks_invalid_fecha(self) -> None:
        record = FacturaRecord(
            clave="506123",
            fecha_emision="Sin registro en BD",
            emisor_nombre="Proveedor Demo",
            estado="sin_xml",
        )

        vm = build_single_vm(record, "todas", Path("C:/demo.pdf"), "--", None)

        self.assertFalse(vm.btn_classify_enabled)
        self.assertIn("fecha fiscal válida", vm.block_reason)

    def test_build_multi_vm_blocks_lote_con_fecha_invalida(self) -> None:
        records = [
            FacturaRecord(clave="1", fecha_emision="11/04/2026", emisor_nombre="Proveedor Demo"),
            FacturaRecord(clave="2", fecha_emision="", emisor_nombre="Proveedor Demo"),
        ]

        vm = build_multi_vm(records)

        self.assertFalse(vm.btn_classify_enabled)
        self.assertIn("fecha fiscal válida", vm.block_reason)

    def test_factura_index_logs_invalid_ignored_xml_errors(self) -> None:
        with self._workspace_tempdir() as tmp:
            client_folder = Path(tmp) / "cliente"
            metadata = client_folder / ".metadata"
            metadata.mkdir(parents=True)
            (metadata / "ignored_xml_errors.json").write_text("{ invalido", encoding="utf-8")

            class DummyXMLCache:
                def __init__(self, cache_file: Path, xml_root: Path) -> None:
                    return None

                def close(self) -> None:
                    return None

            with mock.patch("gestor_contable.core.xml_cache.XMLCacheManager", DummyXMLCache):
                with self.assertLogs("gestor_contable.core.factura_index", level="WARNING") as logs:
                    records = FacturaIndexer().load_period(client_folder, include_pdf_scan=False)

            joined = "\n".join(logs.output)
            self.assertEqual([], records)
            self.assertIn("ignored_xml_errors.json", joined)
            self.assertIn("No se pudo leer", joined)


if __name__ == "__main__":
    unittest.main()
