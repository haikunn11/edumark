import time
import pytest
from backend.app.core.cache import InMemoryCache, compute_image_hash


def test_ut08_cache_set_and_get():
    """UT-08: Cache set + get trả về đúng giá trị."""
    cache = InMemoryCache()
    cache.set("key1", {"question_id": "Q1", "score": 2.5})
    result = cache.get("key1")
    assert result == {"question_id": "Q1", "score": 2.5}


def test_ut09_cache_miss():
    """UT-09: Cache miss trả về None khi key chưa từng được set."""
    cache = InMemoryCache()
    assert cache.get("non_existent_key") is None


def test_ut10_cache_ttl_expiry():
    """UT-10: Cache tự động hết hạn sau khoảng thời gian TTL."""
    cache = InMemoryCache()
    cache.set("short_key", "temporary_value", ttl_seconds=1)
    assert cache.get("short_key") == "temporary_value"
    time.sleep(1.1)
    assert cache.get("short_key") is None


def test_ut11_cache_clear():
    """UT-11: Gọi clear xóa sạch toàn bộ các key trong cache."""
    cache = InMemoryCache()
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    cache.set("k3", "v3")
    cache.clear()
    assert cache.get("k1") is None
    assert cache.get("k2") is None
    assert cache.get("k3") is None


def test_ut12_md5_hash_consistency():
    """UT-12: Cùng một mảng bytes sinh ra đúng một mã MD5 giống nhau."""
    sample_bytes = b"HANDWRITTEN_MATH_EXAM_PAPER_IMAGE_BYTES"
    hash1 = compute_image_hash(sample_bytes)
    hash2 = compute_image_hash(sample_bytes)
    assert hash1 == hash2
    assert len(hash1) == 32


def test_ut13_md5_hash_different_images():
    """UT-13: Hai ảnh khác nhau sinh ra hai mã MD5 khác nhau."""
    img1 = b"IMAGE_NUMBER_1_DATA"
    img2 = b"IMAGE_NUMBER_2_DATA"
    assert compute_image_hash(img1) != compute_image_hash(img2)
