from __future__ import annotations

import contextlib
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from gestor_contable.core.classification_utils import find_orphaned_pdfs
from gestor_contable.core.classifier import (
    recover_orphaned_pdf,
    sha256_file,
)


class OrphanedPDFRecoveryTests(unittest.TestCase):
    @contextlib.contextmanager
    def _workspace_tempdir(self):
        base = Path.cwd() / "gestor_contable" / "data" / "_tmp_test_orphaned_pdf"
        base.mkdir(parents=True, exist_ok=True)
        path = base / uuid4().hex
        path.mkdir()
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def test_find_orphaned_pdfs_marks_missing_destino_as_adoptar_en_sitio(self) -> None:
        with self._workspace_tempdir() as tmp:
            clave = "50612042600310112345600100001010000000001123456789"
            client_name = "CLIENTE TEST"
            contabilidades_root = tmp / "Contabilidades"
            pdf_path = contabilidades_root / "04-ABRIL" / client_name / "COMPRAS" / f"{clave}.pdf"
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(b"%PDF-1.4\nPDF DE PRUEBA\n%%EOF\n")

            xml_path = tmp / "CLIENTES" / client_name / "XML" / f"{clave}.xml"
            xml_path.parent.mkdir(parents=True, exist_ok=True)
            xml_path.write_text("<FacturaElectronica/>", encoding="utf-8")

            db_records = {
                clave: {
                    "ruta_destino": "",
                    "ruta_origen": str(xml_path),
                }
            }

            with self.assertLogs("gestor_contable.core.classification_utils", level="WARNING") as logs:
                orphaned = find_orphaned_pdfs(
                    contabilidades_root,
                    db_records,
                    client_name=client_name,
                )

            self.assertEqual(1, len(orphaned))
            self.assertEqual("adoptar_en_sitio", orphaned[0]["motivo"])
            self.assertIsNone(orphaned[0]["ruta_esperada"])
            self.assertIn("adoptar_en_sitio", "\n".join(logs.output))

    def test_recover_orphaned_pdf_adopts_in_place_when_expected_route_is_xml(self) -> None:
        with self._workspace_tempdir() as tmp:
            clave = "50612042600310112345600100001010000000002123456789"

            xml_path = tmp / "CLIENTES" / "CLIENTE TEST" / "XML" / f"{clave}.xml"
            xml_path.parent.mkdir(parents=True, exist_ok=True)
            xml_content = "<FacturaElectronica><Clave>XML_ORIGINAL</Clave></FacturaElectronica>"
            xml_path.write_text(xml_content, encoding="utf-8")

            pdf_path = tmp / "Contabilidades" / "04-ABRIL" / "CLIENTE TEST" / "COMPRAS" / f"{clave}.pdf"
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_bytes = b"%PDF-1.4\nPDF ADOPTADO\n%%EOF\n"
            pdf_path.write_bytes(pdf_bytes)

            class DummyDB:
                def __init__(self) -> None:
                    self.record = {
                        "clave_numerica": clave,
                        "estado": "pendiente_pdf",
                        "categoria": "COMPRAS",
                        "subtipo": "SERVICIOS",
                        "nombre_cuenta": "Cuenta Demo",
                        "proveedor": "Proveedor Demo",
                        "ruta_origen": str(xml_path),
                        "ruta_destino": "",
                        "sha256": "",
                        "fecha_clasificacion": "2026-04-12T20:00:00",
                        "clasificado_por": "tester",
                        "ors_manual_override": "",
                    }

                def get_record(self, clave_numerica: str) -> dict | None:
                    if clave_numerica == clave:
                        return dict(self.record)
                    return None

                def upsert(self, **kwargs: str) -> None:
                    self.record.update(kwargs)

            db = DummyDB()

            orphaned_info = {
                "clave": clave,
                "archivo": str(pdf_path),
                "ruta_esperada": str(xml_path),
                "motivo": "huerfano_sin_destino",
                "categoria_inferida": "GASTOS",
            }

            self.assertTrue(recover_orphaned_pdf(orphaned_info, db))

            updated = db.get_record(clave)
            self.assertIsNotNone(updated)

            self.assertEqual(xml_content, xml_path.read_text(encoding="utf-8"))
            self.assertEqual(pdf_bytes, pdf_path.read_bytes())
            self.assertEqual("clasificado", updated["estado"])
            self.assertEqual("COMPRAS", updated["categoria"])
            self.assertEqual("SERVICIOS", updated["subtipo"])
            self.assertEqual("Cuenta Demo", updated["nombre_cuenta"])
            self.assertEqual("Proveedor Demo", updated["proveedor"])
            self.assertEqual(str(xml_path), updated["ruta_origen"])
            self.assertEqual(str(pdf_path), updated["ruta_destino"])
            self.assertEqual(sha256_file(pdf_path), updated["sha256"])


if __name__ == "__main__":
    unittest.main()
