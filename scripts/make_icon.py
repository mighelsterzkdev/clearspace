"""Draw the application icon (assets/clearspace.ico) at every Windows size.

Needs Pillow, which the app itself does not use: run it with any Python that has it.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
SCALE = 4  # draw large, then downsample for smooth edges


def sparkle(cx, cy, radius, waist):
    """A four-pointed star with concave sides, sampled as a polygon."""
    points = []
    for tip in range(4):
        start, end = tip * math.pi / 2, (tip + 1) * math.pi / 2
        a = (cx + radius * math.cos(start), cy - radius * math.sin(start))
        b = (cx + radius * math.cos(end), cy - radius * math.sin(end))
        middle = (start + end) / 2
        control = (cx + waist * math.cos(middle), cy - waist * math.sin(middle))
        for step in range(24):
            t = step / 24
            points.append(((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * control[0] + t * t * b[0],
                           (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * control[1] + t * t * b[1]))
    return points


def draw(size):
    canvas = size * SCALE
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    unit = canvas / 256
    margin = 8 * unit
    # Dark rounded tile with a faint edge, matching the app's dark theme.
    pen.rounded_rectangle((margin, margin, canvas - margin, canvas - margin), radius=56 * unit, fill=(10, 10, 10, 255),
                          outline=(64, 64, 70, 255), width=max(1, round(4 * unit)))
    white = (250, 250, 250, 255)
    pen.polygon(sparkle(116 * unit, 140 * unit, 76 * unit, 14 * unit), fill=white)
    pen.polygon(sparkle(184 * unit, 72 * unit, 30 * unit, 6 * unit), fill=white)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main():
    output = ROOT / "assets" / "clearspace.ico"
    output.parent.mkdir(exist_ok=True)
    images = [draw(size) for size in SIZES]
    images[-1].save(output, sizes=[(size, size) for size in SIZES], append_images=images[:-1])
    images[-1].save(output.with_suffix(".png"))
    print(output)


if __name__ == "__main__":
    main()
