from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app3.core.factura_index import FacturaIndexer
from app3.core.models import FacturaRecord


class FacturaIndexerPDFScanTests(unittest.TestCase):
    def test_scan_and_link_pdfs_optimized(self) -> None:
        clave_nombre = "11111111111111111111111111111111111111111111111111"
        clave_subfolder = "22222222222222222222222222222222222222222222222222"
        clave_consecutivo = "33333333333333333333333333333312345678901234567890"
        consecutivo = "12345678901234567890"

        with TemporaryDirectory() as temp_dir:
            pdf_root = Path(temp_dir) / "PDF"
            sender_dir = pdf_root / "SENDER"
            sender_dir.mkdir(parents=True)

            (pdf_root / f"{clave_nombre}.pdf").write_bytes(b"%PDF-1.4 filename-key")
            (pdf_root / "factura_sin_clave_en_nombre.pdf").write_bytes(b"%PDF-1.4 no key here")
            (pdf_root / "CORROMPIDO.pdf").write_bytes(b"NOT_A_REAL_PDF")
            (sender_dir / f"{clave_subfolder}.pdf").write_bytes(b"%PDF-1.4 nested filename-key")
            (pdf_root / f"Factura {consecutivo}.pdf").write_bytes(b"%PDF-1.4 consecutivo in filename")
            (pdf_root / "brochure_bioclean.pdf").write_bytes(b"%PDF-1.4 brochure")

            records = {
                clave_nombre: FacturaRecord(clave=clave_nombre, xml_path=Path("dummy.xml"), estado="pendiente"),
                clave_consecutivo: FacturaRecord(
                    clave=clave_consecutivo,
                    consecutivo=consecutivo,
                    xml_path=Path("dummy2.xml"),
                    estado="pendiente",
                ),
            }

            indexer = FacturaIndexer()

            def fake_extract_clave_and_cedula(data: bytes, original_filename: str = "") -> tuple[str | None, str | None]:
                if original_filename in {"factura_sin_clave_en_nombre.pdf", "CORROMPIDO.pdf", f"Factura {consecutivo}.pdf", "brochure_bioclean.pdf"}:
                    return None, None
                return None, None

            def fake_fallback(pdf_data: bytes) -> tuple[str | None, str, list[str], list[str]]:
                if b"NOT_A_REAL_PDF" in pdf_data:
                    return None, "corrupted", [], []
                return None, "extract_failed", [], []

            with patch("app3.core.factura_index.extract_clave_and_cedula", side_effect=fake_extract_clave_and_cedula):
                with patch.object(FacturaIndexer, "_extract_clave_from_pdf_text", side_effect=fake_fallback):
                    report = indexer._scan_and_link_pdfs_optimized(pdf_root, records)

        self.assertIn("linked", report)
        self.assertIn("omitidos", report)
        self.assertIn("audit", report)

        self.assertEqual(report["audit"]["total_procesados"], 6)
        self.assertEqual(report["audit"]["exitosos"], 3)
        self.assertEqual(report["audit"]["omitidos"], 2)
        self.assertEqual(report["audit"]["ignorados_no_factura"], 1)
        self.assertEqual(report["audit"]["claves_faltantes_pdf"], [])

        self.assertIn(clave_nombre, report["linked"])
        self.assertIn(clave_subfolder, report["linked"])
        self.assertIn(clave_consecutivo, report["linked"])

        self.assertIsNotNone(records[clave_nombre].pdf_path)
        self.assertIn(clave_subfolder, records)
        self.assertEqual(records[clave_subfolder].estado, "sin_xml")
        self.assertIsNotNone(records[clave_consecutivo].pdf_path)

        self.assertEqual(report["omitidos"]["factura_sin_clave_en_nombre.pdf"]["razon"], "extract_failed")
        self.assertEqual(report["omitidos"]["CORROMPIDO.pdf"]["razon"], "corrupted")
        self.assertEqual(report["omitidos"]["brochure_bioclean.pdf"]["razon"], "non_invoice")


class FacturaIndexerMultipleClaveSelectionTests(unittest.TestCase):
    def test_prefers_clave_detectada_que_existe_en_records(self) -> None:
        clave_real = "5" * 50
        clave_ajena = "6" * 50

        with TemporaryDirectory() as temp_dir:
            pdf_root = Path(temp_dir) / "PDF"
            pdf_root.mkdir(parents=True)
            (pdf_root / "documento.pdf").write_bytes(b"%PDF-1.4")

            records = {
                clave_real: FacturaRecord(clave=clave_real, xml_path=Path("x.xml"), estado="pendiente"),
            }
            indexer = FacturaIndexer()

            def fake_extract_clave_and_cedula(data: bytes, original_filename: str = "") -> tuple[str | None, str | None]:
                return None, None

            def fake_fallback(pdf_data: bytes) -> tuple[str | None, str, list[str], list[str]]:
                return None, "extract_failed", [], [clave_ajena, clave_real]

            with patch("app3.core.factura_index.extract_clave_and_cedula", side_effect=fake_extract_clave_and_cedula):
                with patch.object(FacturaIndexer, "_extract_clave_from_pdf_text", side_effect=fake_fallback):
                    report = indexer._scan_and_link_pdfs_optimized(pdf_root, records)

        self.assertIn(clave_real, report["linked"])
        self.assertNotIn(clave_ajena, report["linked"])
        self.assertIsNotNone(records[clave_real].pdf_path)


if __name__ == "__main__":
    unittest.main()
