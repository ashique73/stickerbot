import re
import secrets
import unicodedata

_bot_username: str | None = None


def set_bot_username(username: str) -> None:
    global _bot_username
    _bot_username = username


def get_bot_username() -> str:
    if not _bot_username:
        raise RuntimeError("Bot username has not been initialized")
    return _bot_username


def slugify_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()
    if not slug:
        return "pack"
    if not slug[0].isalpha():
        slug = f"pack_{slug}"
    return slug


def generate_base(title: str, token: str | None = None) -> str:
    slug = slugify_title(title)[:24].strip("_") or "pack"
    return f"{slug}_{token or secrets.token_hex(3)}"


def build_short_name(base: str, segment: int = 1, bot_username: str | None = None) -> str:
    username = (bot_username or get_bot_username()).lower()
    suffix = f"_by_{username}"
    segment_suffix = "" if segment <= 1 else f"_{segment}"
    max_base_length = 64 - len(suffix) - len(segment_suffix)
    if max_base_length < 1:
        raise ValueError("Bot username is too long for a valid sticker set name")

    sanitized = re.sub(r"[^a-zA-Z0-9_]+", "_", base)
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    if not sanitized:
        sanitized = "pack"
    if not sanitized[0].isalpha():
        sanitized = f"pack_{sanitized}"
    sanitized = sanitized[:max_base_length].rstrip("_")

    name = f"{sanitized}{segment_suffix}{suffix}"
    if len(name) > 64:
        raise ValueError("Sticker set name exceeds 64 characters")
    if "__" in name:
        raise ValueError("Sticker set name contains consecutive underscores")
    if not name[0].isalpha():
        raise ValueError("Sticker set name must begin with a letter")
    if not name.endswith(suffix):
        raise ValueError("Sticker set name must end with the bot username suffix")
    return name


def segment_title(title: str, segment: int) -> str:
    if segment <= 1:
        return title[:64]
    suffix = f" ({segment})"
    return f"{title[: 64 - len(suffix)].rstrip()}{suffix}"
