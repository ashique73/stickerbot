import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.services.media import convert_static, convert_video


def test_static_conversion_makes_512_webp(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    destination = tmp_path / "sticker.webp"
    Image.new("RGB", (900, 300), (200, 20, 20)).save(source)

    convert_static(source, destination)

    assert destination.exists()
    assert destination.stat().st_size <= 512 * 1024
    with Image.open(destination) as image:
        assert image.size == (512, 512)
        assert image.format == "WEBP"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_video_conversion_makes_512_webm(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    destination = tmp_path / "sticker.webm"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=640x360:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )

    convert_video(source, destination)

    assert destination.exists()
    assert destination.stat().st_size <= 256 * 1024
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.strip() == "512x512"
    alpha_mode = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream_tags=alpha_mode",
            "-of",
            "csv=p=0",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert alpha_mode.stdout.strip() == "1"
    audio = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert audio.stdout.strip() == ""
