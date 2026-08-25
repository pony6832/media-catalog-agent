from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat


class FrameSelector:
    def select(
        self,
        candidates: Iterable[Path],
        *,
        ocr_text: str = "",
    ) -> tuple[Path, ...]:
        paths = tuple(
            sorted(
                {Path(item).resolve() for item in candidates},
                key=lambda item: str(item).casefold(),
            )
        )
        if not paths:
            raise ValueError("at least one candidate frame is required")
        for path in paths:
            if not path.is_file():
                raise FileNotFoundError(path)

        differences = {
            (left, right): self._difference(left, right)
            for index, left in enumerate(paths)
            for right in paths[index + 1 :]
        }
        maximum_difference = max(differences.values(), default=0.0)
        ocr_length = len("".join(ocr_text.split()))
        if maximum_difference >= 0.5 or ocr_length >= 80:
            target_count = 3
        elif maximum_difference >= 0.08 or ocr_length > 0:
            target_count = 2
        else:
            target_count = 1
        target_count = min(target_count, len(paths), 3)

        sharpness = {path: self._sharpness(path) for path in paths}
        first = min(
            paths,
            key=lambda path: (-sharpness[path], str(path).casefold()),
        )
        selected = [first]
        while len(selected) < target_count:
            remaining = [path for path in paths if path not in selected]
            next_path = min(
                remaining,
                key=lambda path: (
                    -min(
                        self._lookup_difference(path, item, differences)
                        for item in selected
                    ),
                    -sharpness[path],
                    str(path).casefold(),
                ),
            )
            selected.append(next_path)
        return tuple(selected)

    @staticmethod
    def _sharpness(path: Path) -> float:
        with Image.open(path) as source:
            edges = source.convert("L").filter(ImageFilter.FIND_EDGES)
            return float(sum(ImageStat.Stat(edges).var))

    @staticmethod
    def _difference(left: Path, right: Path) -> float:
        with Image.open(left) as left_source, Image.open(right) as right_source:
            left_image = left_source.convert("RGB")
            right_image = right_source.convert("RGB")
            if right_image.size != left_image.size:
                right_image = right_image.resize(
                    left_image.size, Image.Resampling.BILINEAR
                )
            difference = ImageChops.difference(left_image, right_image)
            channel_means = ImageStat.Stat(difference).mean
        return float(sum(channel_means) / len(channel_means) / 255)

    @staticmethod
    def _lookup_difference(
        left: Path,
        right: Path,
        differences: dict[tuple[Path, Path], float],
    ) -> float:
        if left == right:
            return 0.0
        key = (left, right) if (left, right) in differences else (right, left)
        return differences[key]
