from __future__ import annotations

import os
from typing import Protocol
from backend.app.core.config import settings


class StorageEngine(Protocol):
    def save_file(self, filename: str, content: bytes, subfolder: str = "") -> str: ...
    def read_file(self, file_path: str) -> bytes: ...


class LocalStorageEngine:
    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = base_dir or settings.UPLOAD_DIR
        os.makedirs(self.base_dir, exist_ok=True)

    def save_file(self, filename: str, content: bytes, subfolder: str = "") -> str:
        target_dir = os.path.join(self.base_dir, subfolder) if subfolder else self.base_dir
        os.makedirs(target_dir, exist_ok=True)
        file_path = os.path.join(target_dir, filename)
        with open(file_path, "wb") as f:
            f.write(content)
        return file_path

    def read_file(self, file_path: str) -> bytes:
        with open(file_path, "rb") as f:
            return f.read()


storage = LocalStorageEngine()
