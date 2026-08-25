from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from media_catalog.gemini_client import GeminiClient, GeminiSegmentRequest
from media_catalog.inference import Analysis


@pytest.mark.skipif(
    os.getenv("RUN_GEMINI_LIVE_TEST") != "1",
    reason="explicit opt-in required",
)
def test_live_gemini_37_flash(tmp_path: Path) -> None:
    assert os.getenv("GEMINI_API_KEY")
    preview = tmp_path / "preview.jpg"
    Image.new("RGB", (32, 32), "navy").save(preview)
    request = GeminiSegmentRequest(
        frames=(preview,),
        ocr_text="",
        local_analysis=Analysis(
            description="畫面資訊不足",
            highlights=("需要確認",),
            keywords=("測試",),
        ),
    )

    result = GeminiClient().analyze(request)

    assert result.description.strip()
    assert result.highlights
    assert result.keywords
