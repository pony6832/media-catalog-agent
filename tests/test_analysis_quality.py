from __future__ import annotations

from media_catalog.inference import Analysis, assess_analysis_quality


def test_quality_flags_generic_and_ocr_missing_output() -> None:
    issues = assess_analysis_quality(
        Analysis("一個場景。", ("畫面",), ("內容",)),
        ocr_text="會議標題" * 20,
        frame_count=3,
    )

    assert {
        "generic_description",
        "generic_keywords",
        "ocr_not_reflected",
    } <= set(issues)


def test_quality_flags_duplicate_highlights() -> None:
    issues = assess_analysis_quality(
        Analysis(
            "會議室內有人展示季度報表。",
            ("展示報表", " 展示報表 "),
            ("會議", "報表"),
        ),
        ocr_text="",
        frame_count=2,
    )

    assert "duplicate_highlights" in issues


def test_quality_accepts_specific_analysis_that_reflects_ocr() -> None:
    issues = assess_analysis_quality(
        Analysis(
            "會議標題顯示第三季專案進度，講者正在說明圖表。",
            ("第三季進度圖表", "講者簡報"),
            ("會議", "第三季", "專案進度"),
        ),
        ocr_text="會議標題 第三季專案進度 " * 10,
        frame_count=3,
    )

    assert issues == ()


def test_quality_flags_explicitly_conflicting_frame_summaries() -> None:
    issues = assess_analysis_quality(
        Analysis(
            "不同畫面呈現活動現場的變化。",
            ("活動流程",),
            ("活動",),
        ),
        ocr_text="",
        frame_count=3,
        frame_summaries=("白天室外舞台", "夜晚室內會議", "白天室外觀眾"),
    )

    assert "conflicting_frames" in issues
