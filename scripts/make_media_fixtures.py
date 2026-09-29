"""Generate the raw-media fixtures in data/media/ (no transcript, no OCR text).

Speech: synthesised with the Windows SAPI voice (System.Speech) at 16 kHz mono
PCM, so the runtime must run real ASR on it. Frames: rendered appliance
panels with Pillow (texture, noise, slight rotation), so it must run real OCR.

The generated files are committed; re-run this only to change them:

    python scripts/make_media_fixtures.py            # frames everywhere, speech on Windows
"""

from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path("data/media")

SPEECH = {
    "find_flights_delhi.wav": "Find flights to Delhi tomorrow morning.",
    "actually_bangalore.wav": "Actually, Bangalore.",
    "open_a_ticket.wav": "Open a ticket.",
    "printer_jammed.wav": "The printer on floor two is jammed.",
    "repair_on_monday.wav": "Can someone come and repair my washer on Monday?",
    "yes_please.wav": "Yes please.",
}


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _panel(
    lines: list[str], seed: int, *, blur: float = 0.0, contrast: int = 255
) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("RGB", (640, 400), (rng.randint(170, 200),) * 3)
    d = ImageDraw.Draw(img)
    for _ in range(4000):  # brushed-metal texture
        x, y = rng.randrange(640), rng.randrange(400)
        v = rng.randint(150, 215)
        d.point((x, y), fill=(v, v, v))
    d.rounded_rectangle(
        (60, 70, 580, 330), radius=18, fill=(25, 35, 30), outline=(90, 90, 90), width=4
    )
    lit = (min(255, 60 + contrast // 2), contrast, min(255, 80 + contrast // 3))
    y = 105
    for i, line in enumerate(lines):
        d.text((95, y), line, font=_font(64 if i == 0 else 52), fill=lit)
        y += 95
    img = img.rotate(
        rng.uniform(-4, 4), resample=Image.Resampling.BICUBIC, fillcolor=(120, 120, 120)
    )
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    return img


def make_frames() -> None:
    _panel(["WM-4500", "ERROR E4"], seed=7).save(OUT / "washer_panel_e4.png")
    textless = Image.new("RGB", (640, 400), (205, 200, 190))
    d = ImageDraw.Draw(textless)
    # a washer door, no label
    d.ellipse((230, 110, 410, 290), fill=(70, 80, 95), outline=(40, 40, 40), width=6)
    textless.filter(ImageFilter.GaussianBlur(1.2)).save(OUT / "washer_door_no_text.png")
    blurred = _panel(["WM-4500", "ERROR E4"], seed=11, blur=7.0, contrast=110)
    blurred.save(OUT / "washer_panel_blurred.png")


def make_speech() -> None:
    if sys.platform != "win32":
        print("speech fixtures need Windows SAPI; keeping the committed WAVs")
        return
    for name, text in SPEECH.items():
        target = (OUT / name).resolve()
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
            "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
            "[System.Speech.AudioFormat.AudioChannel]::Mono);"
            f"$s.SetOutputToWaveFile('{target}', $f);"
            f"$s.Speak('{text.replace(chr(39), chr(39) * 2)}');"
            "$s.Dispose()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)  # noqa: S603, S607


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    make_frames()
    make_speech()
    for p in sorted(OUT.iterdir()):
        print(f"{p.name:28s} {p.stat().st_size / 1024:7.1f} KB")
