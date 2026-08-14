from __future__ import annotations

from pathlib import Path
from typing import Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import MediaRecord, Status


CATALOG_HEADERS = (
    "狀態",
    "檔名",
    "完整路徑",
    "媒體類型",
    "內容描述",
    "重點",
    "關鍵字",
    "拍攝時間",
    "處理時間",
    "Markdown 路徑",
    "備份路徑",
    "錯誤原因",
)

_STATUS_LABELS = {
    Status.PENDING: "待處理",
    Status.PROCESSING: "處理中",
    Status.COMPLETED: "完成",
    Status.SKIPPED: "略過",
    Status.FAILED: "失敗",
}

_MEDIA_LABELS = {
    "image/jpeg": "照片 (JPEG)",
    "image/png": "照片 (PNG)",
    "image/webp": "照片 (WebP)",
    "image/heic": "照片 (HEIC)",
    "video/mp4": "影片 (MP4)",
    "video/quicktime": "影片 (MOV)",
    "video/x-matroska": "影片 (MKV)",
    "video/webm": "影片 (WebM)",
}


def write_excel(records: Iterable[MediaRecord], output_path: Path) -> Path:
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "媒體清冊"
    sheet.append(CATALOG_HEADERS)

    for record in records:
        sheet.append(
            (
                _STATUS_LABELS[record.status],
                record.path.name,
                str(record.path),
                _MEDIA_LABELS.get(record.media_type, record.media_type),
                record.description,
                "；".join(record.highlights),
                "、".join(record.keywords),
                None,
                record.updated_at if record.status is Status.COMPLETED else None,
                str(record.markdown_path) if record.markdown_path else None,
                str(record.backup_path) if record.backup_path else None,
                record.error,
            )
        )

    header_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center")

    widths = (12, 28, 64, 18, 44, 36, 30, 20, 24, 54, 54, 40)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    workbook.save(destination)
    workbook.close()
    return destination
