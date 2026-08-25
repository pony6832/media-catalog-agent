from __future__ import annotations

import subprocess


HIDDEN_PROCESS_CREATION_FLAGS = getattr(
    subprocess, "CREATE_NO_WINDOW", 0
)
