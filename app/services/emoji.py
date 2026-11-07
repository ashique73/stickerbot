import emoji


def extract_emoji(text: str | None, default: str) -> list[str]:
    fallback = default.strip() or "⭐"
    if not text:
        return [fallback]
    found = [item["emoji"] for item in emoji.emoji_list(text)]
    if not found:
        return [fallback]
    return found[:20]
