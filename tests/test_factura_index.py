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

        with TemporaryDirectory() as temp_dir:
            pdf_root = Path(temp_dir) / "PDF"
            sender_dir = pdf_root / "SENDER"
            sender_dir.mkdir(parents=True)

            # Clave directa en nombre
            (pdf_root / f"{clave_nombre}.pdf").write_bytes(b"%PDF-1.4 filename-key")
            # No trae clave, debe fallar extracción
            (pdf_root / "factura_sin_clave_en_nombre.pdf").write_bytes(b"%PDF-1.4 no key here")
            # Corrupto
            (pdf_root / "CORROMPIDO.pdf").write_bytes(b"NOT_A_REAL_PDF")
            # Subfolder con clave en nombre
            (sender_dir / f"{clave_subfolder}.pdf").write_bytes(b"%PDF-1.4 nested filename-key")

            records = {
                clave_nombre: FacturaRecord(clave=clave_nombre, xml_path=Path("dummy.xml"), estado="pendiente")
            }

            indexer = FacturaIndexer()

            def fake_extract_clave_and_cedula(data: bytes, original_filename: str = "") -> tuple[str | None, str | None]:
                if original_filename == "factura_sin_clave_en_nombre.pdf":
                    return None, None
                if original_filename == "CORROMPIDO.pdf":
                    raise ValueError("cannot parse")
                return None, None

            def fake_fallback(pdf_data: bytes) -> tuple[str | None, str]:
                if b"NOT_A_REAL_PDF" in pdf_data:
                    return None, "corrupted"
                return None, "extract_failed"

            with patch("app3.core.factura_index.extract_clave_and_cedula", side_effect=fake_extract_clave_and_cedula):
                with patch.object(FacturaIndexer, "_extract_clave_from_pdf_text", side_effect=fake_fallback):
                    report = indexer._scan_and_link_pdfs_optimized(pdf_root, records)

        self.assertIn("linked", report)
        self.assertIn("omitidos", report)
        self.assertIn("audit", report)

        self.assertEqual(report["audit"]["total_procesados"], 4)
        self.assertEqual(report["audit"]["exitosos"], 2)
        self.assertEqual(report["audit"]["omitidos"], 2)

        self.assertIn(clave_nombre, report["linked"])
        self.assertIn(clave_subfolder, report["linked"])

        # Registro existente actualizado
        self.assertIsNotNone(records[clave_nombre].pdf_path)
        # Registro nuevo creado sin XML
        self.assertIn(clave_subfolder, records)
        self.assertEqual(records[clave_subfolder].estado, "sin_xml")

        self.assertEqual(
            report["omitidos"]["factura_sin_clave_en_nombre.pdf"]["razon"],
            "extract_failed",
        )
        self.assertEqual(report["omitidos"]["CORROMPIDO.pdf"]["razon"], "corrupted")


if __name__ == "__main__":
    unittest.main()
