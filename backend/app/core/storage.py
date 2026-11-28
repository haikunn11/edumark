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
        base_abs = os.path.abspath(self.base_dir)
        safe_sub = os.path.normpath(subfolder).lstrip("\\/") if subfolder else ""
        if safe_sub == ".":
            safe_sub = ""
        target_dir = os.path.abspath(os.path.join(base_abs, safe_sub)) if safe_sub else base_abs
        if os.path.commonpath([base_abs, target_dir]) != base_abs:
            raise ValueError(f"Đường dẫn lưu trữ không hợp lệ: {subfolder}")
        os.makedirs(target_dir, exist_ok=True)
        safe_name = os.path.basename(filename)
        if not safe_name:
            raise ValueError("Tên file lưu trữ không hợp lệ.")
        file_path = os.path.abspath(os.path.join(target_dir, safe_name))
        if os.path.commonpath([target_dir, file_path]) != target_dir:
            raise ValueError(f"Tên file không hợp lệ: {filename}")
        with open(file_path, "wb") as f:
            f.write(content)
        return file_path

    def read_file(self, file_path: str) -> bytes:
        with open(file_path, "rb") as f:
            return f.read()


storage = LocalStorageEngine()
