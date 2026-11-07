import asyncio
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile
from PIL import Image, ImageOps

from app.services.errors import StickerConversionError, UnsupportedMediaError
from app.services.telegram import call_with_retry

STATIC_MAX_BYTES = 512 * 1024
VIDEO_MAX_BYTES = 256 * 1024
ANIMATED_MAX_BYTES = 64 * 1024
DOWNLOAD_MAX_BYTES = 20 * 1024 * 1024


@dataclass(slots=True)
class MediaSource:
    file_id: str
    format: str
    suffix: str = ""
    existing: bool = False
    emoji_override: list[str] | None = None
    max_download_bytes: int = DOWNLOAD_MAX_BYTES


def convert_static(source: Path, destination: Path) -> None:
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGBA")
        image = ImageOps.contain(image, (512, 512), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        offset = ((512 - image.width) // 2, (512 - image.height) // 2)
        canvas.alpha_composite(image, offset)
        for quality in (95, 90, 85, 80, 75, 70, 65, 60, 55, 50, 45, 40, 35, 30):
            canvas.save(destination, "WEBP", quality=quality, method=6)
            if destination.stat().st_size <= STATIC_MAX_BYTES:
                return
    raise StickerConversionError("Could not fit the image under 512 KB")


def _run_ffmpeg(arguments: list[str], timeout: int) -> None:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise StickerConversionError("ffmpeg is not installed")
    try:
        result = subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise StickerConversionError("Video conversion took too long") from error
    if result.returncode != 0:
        stderr = result.stderr.strip()
        message = stderr.splitlines()[-1] if stderr else "ffmpeg failed"
        raise StickerConversionError(message[-500:])


def convert_video(source: Path, destination: Path) -> None:
    attempts = ((30, 30), (36, 30), (42, 28), (48, 24), (52, 20))
    started = time.monotonic()
    for crf, fps in attempts:
        remaining = 40 - (time.monotonic() - started)
        if remaining < 5:
            break
        filter_graph = (
            "scale=512:512:force_original_aspect_ratio=decrease,"
            "pad=512:512:(ow-iw)/2:(oh-ih)/2:color=0x00000000,"
            f"setsar=1,fps={fps},format=yuva420p"
        )
        _run_ffmpeg(
            [
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-t",
                "3",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                filter_graph,
                "-c:v",
                "libvpx-vp9",
                "-b:v",
                "0",
                "-crf",
                str(crf),
                "-pix_fmt",
                "yuva420p",
                "-auto-alt-ref",
                "0",
                "-row-mt",
                "1",
                "-threads",
                "2",
                "-deadline",
                "good",
                "-cpu-used",
                "5",
                str(destination),
            ],
            timeout=min(30, int(remaining)),
        )
        if destination.stat().st_size <= VIDEO_MAX_BYTES:
            return
    raise StickerConversionError("Could not fit the video under 256 KB")


async def download_media(
    bot: Bot, file_id: str, destination: Path, max_bytes: int = DOWNLOAD_MAX_BYTES
) -> None:
    telegram_file = await call_with_retry(lambda: bot.get_file(file_id))
    if telegram_file.file_size is not None and telegram_file.file_size > max_bytes:
        raise UnsupportedMediaError("That file is too large for a sticker")
    await call_with_retry(
        lambda: bot.download_file(telegram_file.file_path, destination=str(destination))
    )
    if destination.stat().st_size > max_bytes:
        raise UnsupportedMediaError("That file is too large for a sticker")


async def upload_sticker(bot: Bot, user_id: int, path: Path, sticker_format: str) -> str:
    result = await call_with_retry(
        lambda: bot.upload_sticker_file(
            user_id=user_id,
            sticker=FSInputFile(path),
            sticker_format=sticker_format,
        )
    )
    return result.file_id


async def prepare_sticker(bot: Bot, user_id: int, source: MediaSource, workdir: Path) -> str:
    if source.existing:
        return source.file_id

    if source.format not in {"static", "video", "animated"}:
        raise UnsupportedMediaError("Unsupported sticker format")

    input_path = workdir / f"input{source.suffix}"
    await download_media(bot, source.file_id, input_path, source.max_download_bytes)

    if source.format == "animated":
        if input_path.stat().st_size > ANIMATED_MAX_BYTES:
            raise UnsupportedMediaError("Animated stickers must be 64 KB or smaller")
        output_path = input_path
    elif source.format == "static":
        output_path = workdir / "sticker.webp"
        await asyncio.to_thread(convert_static, input_path, output_path)
    else:
        output_path = workdir / "sticker.webm"
        await asyncio.to_thread(convert_video, input_path, output_path)

    return await upload_sticker(bot, user_id, output_path, source.format)
