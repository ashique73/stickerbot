class StickerBotError(Exception):
    pass


class StickerConversionError(StickerBotError):
    pass


class UnsupportedMediaError(StickerBotError):
    pass
