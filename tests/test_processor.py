from pathlib import Path

from media_catalog.database import CatalogDatabase
from media_catalog.inference import Analysis, AnalysisError
from media_catalog.models import Status
from media_catalog.processor import process_selected


class FailingAnalyzer:
    def analyze(self, _: Path) -> Analysis:
        raise AnalysisError("model returned malformed JSON")


class SuccessfulAnalyzer:
    def analyze(self, _: Path) -> Analysis:
        return Analysis(
            description="一張海邊夕陽照片。",
            highlights=("海浪", "夕陽"),
            keywords=("海邊", "黃昏"),
        )


def _add(database: CatalogDatabase, path: Path, fingerprint: str) -> str:
    path.write_bytes(fingerprint.encode("ascii"))
    return database.upsert_discovered(path, fingerprint, "image/jpeg").id


def test_process_selected_leaves_unselected_records_untouched_on_failure(
    tmp_path: Path,
) -> None:
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    selected_id = _add(database, tmp_path / "selected.jpg", "one")
    untouched_id = _add(database, tmp_path / "untouched.jpg", "two")

    process_selected([selected_id], database, FailingAnalyzer())

    assert database.get_record(selected_id).status is Status.FAILED
    assert "malformed JSON" in database.get_record(selected_id).error
    assert database.get_record(untouched_id).status is Status.PENDING


def test_process_selected_persists_analysis_without_marking_complete(
    tmp_path: Path,
) -> None:
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    selected_id = _add(database, tmp_path / "selected.jpg", "one")

    process_selected([selected_id], database, SuccessfulAnalyzer())

    record = database.get_record(selected_id)
    assert record.status is Status.PROCESSING
    assert record.description == "一張海邊夕陽照片。"
    assert record.highlights == ("海浪", "夕陽")
    assert record.keywords == ("海邊", "黃昏")
