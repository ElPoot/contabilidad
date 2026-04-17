"""Tests de regresion para el fallback de encoding XML (caso FAPEMO).

Verifica que XMLs con encoding="UTF-8" declarado pero bytes Latin-1
se recuperen correctamente via re-encoding iso-8859-1 -> utf-8.
"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from gestor_contable.core.xml_manager import CRXMLManager
from gestor_contable.core.pdf_generator import extract_items_from_xml


# XML minimo con encoding declarado UTF-8 pero byte Latin-1 (0xF1 = 'n' tilde)
_LATIN1_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b"<FacturaElectronica>\n"
    b"  <Clave>50601011900310112345600100010100000000011199999999</Clave>\n"
    b"  <FechaEmision>2025-01-01T00:00:00-06:00</FechaEmision>\n"
    b"  <Emisor><Nombre>Compa\xf1ia FAPEMO</Nombre>"
    b"<Identificacion><Tipo>01</Tipo><Numero>3101123456</Numero></Identificacion></Emisor>\n"
    b"  <Receptor><Nombre>Cliente</Nombre>"
    b"<Identificacion><Tipo>01</Tipo><Numero>3101654321</Numero></Identificacion></Receptor>\n"
    b"  <DetalleServicio>\n"
    b"    <LineaDetalle><Cantidad>1</Cantidad><Detalle>Servicio</Detalle>"
    b"<UnidadMedida>Sp</UnidadMedida><MontoTotal>1000</MontoTotal></LineaDetalle>\n"
    b"  </DetalleServicio>\n"
    b"  <ResumenFactura><TotalComprobante>1000</TotalComprobante></ResumenFactura>\n"
    b"</FacturaElectronica>\n"
)

# XML valido UTF-8 (sin bytes corruptos)
_VALID_XML = _LATIN1_XML.replace(b"Compa\xf1ia", b"Compania")


class XmlEncodingFallbackTests(unittest.TestCase):
    """Verifica que _safe_parse_xml_file recupere XMLs con encoding corrupto."""

    def setUp(self) -> None:
        self._tmp = Path.cwd() / "gestor_contable" / "data" / f"_tmp_test_encoding_{uuid4().hex}"
        self._tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_xml(self, content: bytes, name: str = "test.xml") -> Path:
        p = self._tmp / name
        p.write_bytes(content)
        return p

    def test_safe_parse_recovers_latin1_xml(self) -> None:
        """_safe_parse_xml_file debe recuperar XML con bytes Latin-1."""
        xml_path = self._write_xml(_LATIN1_XML)
        mgr = CRXMLManager()
        result = mgr._safe_parse_xml_file(xml_path)
        self.assertEqual(result["_process_status"], "ok")
        self.assertIn("Clave", result.get("FacturaElectronica_Clave", ""))

    def test_safe_parse_valid_utf8(self) -> None:
        """_safe_parse_xml_file parsea UTF-8 valido sin fallback."""
        xml_path = self._write_xml(_VALID_XML)
        mgr = CRXMLManager()
        result = mgr._safe_parse_xml_file(xml_path)
        self.assertEqual(result["_process_status"], "ok")

    def test_pdf_generator_recovers_latin1_xml(self) -> None:
        """extract_items_from_xml debe recuperar items de XML con bytes Latin-1."""
        xml_path = self._write_xml(_LATIN1_XML)
        items = extract_items_from_xml(xml_path)
        self.assertIsNotNone(items)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["desc"], "Servicio")

    def test_pdf_generator_valid_utf8(self) -> None:
        """extract_items_from_xml parsea UTF-8 valido sin problemas."""
        xml_path = self._write_xml(_VALID_XML)
        items = extract_items_from_xml(xml_path)
        self.assertIsNotNone(items)


if __name__ == "__main__":
    unittest.main()
