from pathlib import Path

from app3.core.catalog import CatalogManager
from app3.core.classifier import ClassificationDB, classify_record
from app3.core.models import FacturaRecord


def test_catalog_atomic_save_load(tmp_path: Path):
    manager = CatalogManager(tmp_path)
    data = {"COMPRAS": {"CONTADO": {}}}
    manager.save(data)
    loaded = manager.load()
    assert loaded == data


def test_classify_record_moves_pdf_and_registers(tmp_path: Path):
    client = tmp_path / "CLIENTE"
    pdf = client / "PDF" / "a.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"fake-pdf")

    record = FacturaRecord(clave="5" * 50, pdf_path=pdf)
    db = ClassificationDB(client / ".metadata")

    target = classify_record(record, client, db, "COMPRAS", "CONTADO", "PROVEEDOR")
    assert target is not None
    assert target.exists()
    assert not pdf.exists()
    assert db.get_estado(record.clave) == "clasificado"
