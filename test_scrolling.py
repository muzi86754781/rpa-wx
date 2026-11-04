import random
import unittest
from PIL import Image, ImageDraw
from scrolling import content_direction

BOX = dict(left=50, top=100, right=400, bottom=900)


class MotionTests(unittest.TestCase):
    def image(self, offset, pinned=False):
        source = Image.new('RGB', (210, 1800), 'white')
        draw = ImageDraw.Draw(source)
        randomizer = random.Random(42)
        for y in range(0, 1800, 4):
            draw.line((12, y, randomizer.randrange(60, 195), y),
                      fill=(randomizer.randrange(30, 150),) * 3, width=2)
        result = Image.new('RGB', (600, 600), 'white')
        result.paste(source.crop((0, offset, 210, offset + 480)), (30, 60))
        if pinned:
            ImageDraw.Draw(result).rectangle((30, 60, 240, 150), fill='gray')
        return result

    def test_content_moves_up_when_later_rows_are_revealed(self):
        self.assertEqual(content_direction(self.image(100), self.image(160), BOX), -1)

    def test_content_moves_down_when_earlier_rows_are_revealed(self):
        self.assertEqual(content_direction(self.image(160), self.image(100), BOX), 1)

    def test_static_pinned_area_does_not_reverse_the_measurement(self):
        self.assertEqual(content_direction(self.image(100, True), self.image(160, True), BOX), -1)

    def test_unchanged_and_uniform_images_are_inconclusive(self):
        a = self.image(100)
        self.assertEqual(content_direction(a, a, BOX), 0)
        self.assertEqual(content_direction(Image.new('RGB', (600, 600)),
                                           Image.new('RGB', (600, 600), 'white'), BOX), 0)

    def test_tiny_regions_do_not_produce_a_guessed_direction(self):
        image = self.image(100)
        self.assertEqual(content_direction(image, image, dict(left=10, right=11, top=10, bottom=11)), 0)
