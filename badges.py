"""Screenshot evidence for numbered badges and the row selected by a click."""
from dataclasses import dataclass
import hashlib


def badge_red(pixel):
    red, green, blue = pixel
    return red >= 205 and 35 <= green <= 140 and 35 <= blue <= 140 and abs(green - blue) <= 34


def pixels(image, box):
    return image.crop(box).convert('RGB').tobytes()


@dataclass(frozen=True)
class Badge:
    bounds: tuple

    @property
    def height(self):
        return self.bounds[3] - self.bounds[1]

    @property
    def point(self):
        left, top, right, bottom = self.bounds
        # The badge straddles the avatar's upper-right corner.
        return round((left + right) / 2 - 1.1 * self.height), round((top + bottom) / 2 + 1.15 * self.height)

    @property
    def avatar(self):
        x, y = self.point
        radius = max(2, self.height // 3)
        return x - radius, y - radius, x + radius, y + radius

    @property
    def gutter(self):
        left, top, right, bottom = self.bounds
        x = round((left + right) / 2 + .44 * self.height)
        y = self.point[1]
        return x, y - 2, x + 2, y + 3

    def signature(self, image):
        return hashlib.sha256(pixels(image, self.avatar)).digest()

    def red_cleared(self, image):
        crop = image.crop(self.bounds).convert('RGB')
        return sum(badge_red(color) for color in crop.getdata()) < crop.width * crop.height * .14


def numbered_candidates(image, box):
    """Find red badge shapes containing a light glyph in the avatar lane."""
    if not box:
        return []
    left = round(image.width * box['left'] / 1000)
    right = round(image.width * box['right'] / 1000)
    top = round(image.height * box['top'] / 1000)
    bottom = round(image.height * box['bottom'] / 1000)
    width = right - left
    region_top = max(0, top - max(12, width // 10))
    strip = image.crop((left, region_top, left + round(width * .49), bottom)).convert('RGB')
    remaining = {(index % strip.width, index // strip.width)
                 for index, color in enumerate(strip.getdata()) if badge_red(color)}
    found = []
    while remaining:
        component = [remaining.pop()]
        for x, y in component:
            for point in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if point in remaining:
                    remaining.remove(point)
                    component.append(point)
        x0 = min(p[0] for p in component)
        y0 = min(p[1] for p in component)
        x1 = max(p[0] for p in component) + 1
        y1 = max(p[1] for p in component) + 1
        w, h = x1 - x0, y1 - y0
        if not (8 <= h <= min(64, width * .22) and .7 * h <= w <= 2.5 * h
                and .35 <= len(component) / (w * h) <= .86):
            continue
        light = 0
        center_x, center_y = (x0 + x1) / 2, (y0 + y1) / 2
        for y in range(y0 + 2, y1 - 2):
            for x in range(x0 + 2, x1 - 2):
                dx = (x + .5 - center_x) / (w * .4)
                dy = (y + .5 - center_y) / (h * .4)
                if dx * dx + dy * dy <= 1:
                    light += min(strip.getpixel((x, y))) >= 235
        if light < max(2, h * h // 40):
            continue
        badge = Badge((left + x0, region_top + y0, left + x1, region_top + y1))
        x, y = badge.point
        if left < x < right and top < y < bottom and badge.avatar[0] >= left and badge.gutter[2] < right:
            found.append(badge)
    return sorted(found, key=lambda badge: badge.point[1])


@dataclass(frozen=True)
class OpenedRow:
    badge: Badge
    avatar_pixels: bytes
    selected_gutter: bytes
    highlighted: bool

    @classmethod
    def confirm(cls, badge, before, after):
        avatar = pixels(before, badge.avatar)
        if before.size != after.size or avatar != pixels(after, badge.avatar) or not badge.red_cleared(after):
            return None
        old = pixels(before, badge.gutter)
        new = pixels(after, badge.gutter)
        # A consistent row-background change supports a title discrepancy.
        solid = len(set(zip(new[::3], new[1::3], new[2::3]))) <= 3
        highlighted = solid and all(max(abs(old[i + j] - new[i + j]) for j in range(3)) >= 8
                                    for i in range(0, len(old), 3))
        return cls(badge, avatar, new, highlighted)

    def matches(self, image):
        return (pixels(image, self.badge.avatar) == self.avatar_pixels and self.badge.red_cleared(image)
                and (not self.highlighted or pixels(image, self.badge.gutter) == self.selected_gutter))
