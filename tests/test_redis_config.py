import pytest

from redis_config import normalize_redis_storage_uri


def test_accepts_standard_tls_uri():
    uri = "rediss://default:secret@redis.example.com:6379"
    assert normalize_redis_storage_uri(uri, is_production=True) == uri


def test_strips_pasted_env_assignment():
    uri = "rediss://default:secret@redis.example.com:6379"
    assert normalize_redis_storage_uri(
        'REDIS_URL="' + uri + '"', is_production=True
    ) == uri


def test_rejects_read_only_user_without_echoing_secret():
    uri = "REDIS_URL=rediss://default_ro:must-not-leak@redis.example.com:6379"
    with pytest.raises(RuntimeError) as error:
        normalize_redis_storage_uri(uri, is_production=True)
    assert "solo lectura" in str(error.value)
    assert "must-not-leak" not in str(error.value)


def test_rejects_invalid_scheme_without_echoing_secret():
    uri = "REDIS_URL=redis_bad://default:must-not-leak@redis.example.com:6379"
    with pytest.raises(RuntimeError) as error:
        normalize_redis_storage_uri(uri, is_production=True)
    assert "formato inválido" in str(error.value)
    assert "must-not-leak" not in str(error.value)


def test_memory_is_only_default_for_nonproduction():
    assert normalize_redis_storage_uri(None, is_production=False) == "memory://"


def test_production_requires_redis():
    with pytest.raises(RuntimeError, match="REDIS_URL debe configurarse"):
        normalize_redis_storage_uri(None, is_production=True)
