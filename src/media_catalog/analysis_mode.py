from __future__ import annotations

from enum import Enum


class AnalysisMode(str, Enum):
    AUTO = "auto"
    FORCE_GEMINI = "force-gemini"

    @property
    def label(self) -> str:
        if self is AnalysisMode.FORCE_GEMINI:
            return "Gemini 強制強化"
        return "普通分析"
