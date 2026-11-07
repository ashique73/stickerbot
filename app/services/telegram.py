import asyncio
from collections.abc import Awaitable, Callable

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError


async def call_with_retry[T](
    action: Callable[[], Awaitable[T]], attempts: int = 5, base_delay: float = 1.0
) -> T:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await action()
        except TelegramRetryAfter as error:
            last_error = error
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(error.retry_after + 1)
        except (TelegramNetworkError, TelegramServerError) as error:
            last_error = error
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(base_delay * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError("Telegram request failed without an error")
