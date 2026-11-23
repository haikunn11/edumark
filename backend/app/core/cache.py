from __future__ import annotations

import hashlib
import time
from typing import Any, Protocol


class CacheStore(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl_seconds: int = 3600) -> None: ...
    def delete(self, key: str) -> None: ...


class InMemoryCache:
    """Bộ nhớ đệm trong RAM với TTL (Time-To-Live) tự động dọn dẹp."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._expiry: dict[str, float] = {}

    def get(self, key: str) -> Any | None:
        now = time.time()
        if key in self._expiry and now > self._expiry[key]:
            self.delete(key)
            return None
        return self._store.get(key)

    def set(self, key: str, value: Any, ttl_seconds: int = 3600) -> None:
        self._store[key] = value
        self._expiry[key] = time.time() + ttl_seconds

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._expiry.pop(key, None)

    def clear(self) -> None:
        self._store.clear()
        self._expiry.clear()


def compute_image_hash(image_bytes: bytes) -> str:
    """Tạo MD5 fingerprint cho ảnh bài làm để kiểm tra cache trùng lặp."""
    return hashlib.md5(image_bytes).hexdigest()


# Singleton instance cho toàn bộ ứng dụng
cache = InMemoryCache()
