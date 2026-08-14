from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence

from .database import CatalogDatabase
from .inference import Analysis, AnalysisError
from .models import Status


class Analyzer(Protocol):
    def analyze(self, source: Path) -> Analysis: ...


def process_selected(
    record_ids: Sequence[str], database: CatalogDatabase, analyzer: Analyzer
) -> None:
    for record_id in record_ids:
        record = database.get_record(record_id)
        if record is None:
            raise KeyError(f"Unknown media record: {record_id}")
        if record.status is not Status.PENDING:
            continue
        database.set_status(record_id, Status.PROCESSING)
        try:
            analysis = analyzer.analyze(record.path)
        except AnalysisError as error:
            database.set_status(record_id, Status.FAILED, error=str(error))
            continue
        database.save_analysis(
            record_id,
            description=analysis.description,
            highlights=analysis.highlights,
            keywords=analysis.keywords,
        )
