try:
    from rembg import remove as _rembg_remove
    REMBG_AVAILABLE = True
except Exception:
    REMBG_AVAILABLE = False

from io import BytesIO
from typing import Tuple

from PIL import Image, ImageOps


def remove_background(image_bytes: bytes) -> Tuple[bytes, int, int]:
    if REMBG_AVAILABLE:
        try:
            result = _rembg_remove(image_bytes)
            img = Image.open(BytesIO(result))
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue(), img.width, img.height
        except Exception:
            pass

    img = Image.open(BytesIO(image_bytes))
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    width, height = img.size

    bg_color = _sample_background_color(img)
    mask = _build_bg_mask(img, bg_color)

    result = Image.new("RGBA", img.size, (255, 255, 255, 0))
    result.paste(img, mask=mask)

    buf = BytesIO()
    result.save(buf, format="PNG")
    return buf.getvalue(), width, height


def _sample_background_color(img: Image.Image) -> Tuple[int, int, int]:
    corners = [
        img.getpixel((0, 0)),
        img.getpixel((img.width - 1, 0)),
        img.getpixel((0, img.height - 1)),
        img.getpixel((img.width - 1, img.height - 1)),
    ]
    rgb = []
    for c in corners:
        rgb.append(c[0])
        rgb.append(c[1])
        rgb.append(c[2])
    r = sum(rgb[0::3]) // 4
    g = sum(rgb[1::3]) // 4
    b = sum(rgb[2::3]) // 4
    return (r, g, b)


def _build_bg_mask(img: Image.Image, bg_color: Tuple[int, int, int]) -> Image.Image:
    threshold = 45
    pixels = img.load()
    mask = Image.new("L", img.size, 0)
    mask_pixels = mask.load()

    for y in range(img.height):
        for x in range(img.width):
            pixel = pixels[x, y]
            if len(pixel) >= 4:
                r, g, b, a = pixel[0], pixel[1], pixel[2], pixel[3]
            else:
                r, g, b, a = pixel[0], pixel[1], pixel[2], 255
            if a < 10:
                mask_pixels[x, y] = 0
                continue
            distance = ((r - bg_color[0]) ** 2 + (g - bg_color[1]) ** 2 + (b - bg_color[2]) ** 2) ** 0.5
            if distance < threshold:
                mask_pixels[x, y] = 0
            else:
                mask_pixels[x, y] = 255

    return mask
