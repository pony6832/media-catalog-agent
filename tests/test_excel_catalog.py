from pathlib import Path

from openpyxl import load_workbook

from media_catalog.database import CatalogDatabase
from media_catalog.excel_catalog import CATALOG_HEADERS, write_excel


def test_write_excel_uses_spec_columns_and_persisted_values(tmp_path: Path) -> None:
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    photo_path = tmp_path / "旅行照片.jpg"
    photo_path.write_bytes(b"photo")
    record = database.upsert_discovered(photo_path, "abc", "image/jpeg")
    output = tmp_path / "媒體清冊.xlsx"

    saved_path = write_excel([record], output)

    workbook = load_workbook(saved_path, read_only=True)
    sheet = workbook["媒體清冊"]
    assert [cell.value for cell in sheet[1]] == list(CATALOG_HEADERS)
    assert sheet.cell(2, 1).value == "待處理"
    assert sheet.cell(2, 2).value == "旅行照片.jpg"
    assert sheet.cell(2, 3).value == str(photo_path.resolve())
    assert sheet.cell(2, 4).value == "照片 (JPEG)"
    workbook.close()
