import pytest

from app.services.short_names import (
    build_short_name,
    generate_base,
    segment_title,
    set_bot_username,
    slugify_title,
)


@pytest.fixture(autouse=True)
def bot_username() -> None:
    set_bot_username("MyStickerBot")


def test_generated_name_is_valid() -> None:
    name = build_short_name(generate_base("My Cool Pack!"), 1)
    assert name.endswith("_by_mystickerbot")
    assert len(name) <= 64
    assert name[0].isalpha()
    assert "__" not in name


def test_long_names_are_truncated() -> None:
    name = build_short_name("x" * 100, 12)
    assert name.endswith("_12_by_mystickerbot")
    assert len(name) <= 64
    assert "__" not in name


def test_large_segment_is_inserted_before_suffix() -> None:
    name = build_short_name("pack_abc123", 2)
    assert name == "pack_abc123_2_by_mystickerbot"


def test_slugify_handles_unicode_and_punctuation() -> None:
    assert slugify_title("Café Pack!!!") == "cafe_pack"


def test_segment_title() -> None:
    assert segment_title("Cats", 1) == "Cats"
    assert segment_title("Cats", 2) == "Cats (2)"
    title = segment_title("a" * 64, 12)
    assert len(title) <= 64
    assert title.endswith(" (12)")
