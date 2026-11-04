"""Measure list content movement without relying on device scroll settings."""
from PIL import ImageChops, ImageStat, ImageFilter


def content_direction(before, after, box):
    """Return +1 for content moving down, -1 for up, 0 if ambiguous.

    Match overlapping list content, excluding the scrollbar and most pinned
    rows. Uniform images, repeated patterns and large jumps are inconclusive.
    """
    def sample(image):
        left, top, right, bottom = (image.width * box['left'] / 1000,
                                   image.height * box['top'] / 1000,
                                   image.width * box['right'] / 1000,
                                   image.height * box['bottom'] / 1000)
        width, height = right - left, bottom - top
        crop = image.crop((round(left + width * .06), round(top + height * .2),
                           round(right - width * .1), round(bottom - height * .04)))
        if crop.width < 2 or crop.height < 8:
            return None
        return crop.convert('L').resize((96, min(300, max(24, crop.height)))).filter(ImageFilter.GaussianBlur(1))

    a, b = sample(before), sample(after)
    if a is None or b is None or ImageStat.Stat(a).stddev[0] < 5 or a.size != b.size:
        return 0
    baseline = ImageStat.Stat(ImageChops.difference(a, b)).mean[0]
    if baseline < 1:
        return 0
    width, height = a.size
    candidates = []
    for shift in range(-int(height * .45), int(height * .45) + 1):
        if abs(shift) < 3:
            continue
        upper, lower = abs(shift), height - abs(shift)
        first = a.crop((0, 0 if shift > 0 else upper, width, lower if shift > 0 else height))
        second = b.crop((0, upper if shift > 0 else 0, width, height if shift > 0 else lower))
        error = ImageStat.Stat(ImageChops.difference(first, second)).mean[0]
        candidates.append((error, abs(shift), 1 if shift > 0 else -1))
    best = min(candidates)
    opposite = min(item[0] for item in candidates if item[2] != best[2])
    return best[2] if best[0] < baseline * .55 and best[0] < opposite * .7 else 0
