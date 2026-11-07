from app.services.emoji import extract_emoji


def test_default_emoji_is_used_without_caption() -> None:
    assert extract_emoji(None, "⭐") == ["⭐"]


def test_default_emoji_is_used_without_emoji_in_caption() -> None:
    assert extract_emoji("hello", "⭐") == ["⭐"]


def test_multiple_emoji_are_extracted() -> None:
    assert extract_emoji("😀🔥", "⭐") == ["😀", "🔥"]


def test_emoji_list_is_limited() -> None:
    assert len(extract_emoji("😀" * 30, "⭐")) == 20


def test_blank_default_emoji_falls_back_to_star() -> None:
    assert extract_emoji(None, "  ") == ["⭐"]
