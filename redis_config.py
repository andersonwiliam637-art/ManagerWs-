"""Safe Redis storage URI parsing for ManagerWS rate limiting.

Never include a credential-bearing URI in validation errors or logs.
"""
from urllib.parse import urlsplit


def normalize_redis_storage_uri(raw_value: str | None, *, is_production: bool) -> str:
    """Return a validated Redis URI, accepting a pasted .env assignment safely."""
    value = (raw_value or "").strip()

    # Render expects only the VALUE field, but tolerate users pasting a full
    # .env assignment so an accidental REDIS_URL= prefix cannot crash Flask-Limiter.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    if value.upper().startswith("REDIS_URL="):
        value = value.split("=", 1)[1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()

    if not value or value == "memory://":
        if is_production:
            raise RuntimeError(
                "REDIS_URL debe configurarse en producción con una URL Redis rediss:// o redis://."
            )
        return "memory://"

    try:
        parsed = urlsplit(value)
        # Accessing .port validates the port syntax without exposing the URI.
        _ = parsed.port
    except ValueError:
        raise RuntimeError(
            "REDIS_URL tiene un formato inválido. Usa únicamente la URL Redis, sin asignación ni comillas."
        ) from None

    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise RuntimeError(
            "REDIS_URL tiene un formato inválido. El valor debe comenzar con redis:// o rediss://; "
            "no incluyas REDIS_URL= ni comillas."
        )

    if parsed.username == "default_ro":
        raise RuntimeError(
            "REDIS_URL usa credenciales Redis de solo lectura (default_ro). "
            "Flask-Limiter necesita credenciales estándar con permisos de escritura."
        )

    if is_production and (not parsed.username or not parsed.password):
        raise RuntimeError(
            "REDIS_URL en producción debe incluir las credenciales de conexión Redis."
        )

    return value
