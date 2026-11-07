from app.config import Settings


def test_database_url_is_normalized_for_asyncpg() -> None:
    settings = Settings(
        bot_token="token",
        database_url=(
            "postgres://user:pass@example.com:5432/db?sslmode=require&channel_binding=require"
        ),
    )
    assert settings.database_url == (
        "postgresql+asyncpg://user:pass@example.com:5432/db?ssl=require"
    )


def test_database_url_keeps_other_query_parameters() -> None:
    settings = Settings(
        bot_token="token",
        database_url="postgresql://user:pass@example.com/db?foo=1&sslmode=verify-full",
    )
    assert settings.database_url == (
        "postgresql+asyncpg://user:pass@example.com/db?foo=1&ssl=verify-full"
    )


def test_media_limit_defaults_to_ten_megabytes() -> None:
    settings = Settings(
        bot_token="token",
        database_url="postgresql://user:pass@example.com/db",
    )
    assert settings.max_media_bytes == 10 * 1024 * 1024


def test_webhook_url_prefers_explicit_url() -> None:
    settings = Settings(
        bot_token="token",
        database_url="postgresql://user:pass@example.com/db",
        webhook_url="https://bot.example.com/custom",
        public_base_url="https://bot.example.com",
    )
    assert settings.resolved_webhook_url == "https://bot.example.com/custom"


def test_webhook_url_is_built_from_public_base_url() -> None:
    settings = Settings(
        bot_token="token",
        database_url="postgresql://user:pass@example.com/db",
        public_base_url="https://bot.example.com/",
    )
    assert settings.resolved_webhook_url == "https://bot.example.com/webhook"


def test_webhook_url_is_optional() -> None:
    settings = Settings(
        bot_token="token",
        database_url="postgresql://user:pass@example.com/db",
    )
    assert settings.resolved_webhook_url is None
