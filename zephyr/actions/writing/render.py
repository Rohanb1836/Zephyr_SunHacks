"""Render normalized handwriting strokes to a PNG in memory."""
from io import BytesIO


def render_strokes(strokes, image_long_side=1024, stroke_px=6):
    from PIL import Image, ImageDraw
    points = [point for stroke in strokes for point in stroke]
    if not points:
        raise ValueError('No stroke points')
    xs, ys = zip(*points)
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    width, height = max_x - min_x, max_y - min_y
    long_side = max(width, height, 1e-6)
    padding = long_side * .08
    width, height = width + 2 * padding, height + 2 * padding
    scale = float(image_long_side) / max(width, height)
    image = Image.new('RGB', (max(1, round(width * scale)), max(1, round(height * scale))), 'white')
    draw = ImageDraw.Draw(image)
    radius = stroke_px / 2
    for stroke in strokes:
        pixels = [((x - min_x + padding) * scale, (y - min_y + padding) * scale) for x, y in stroke]
        if len(pixels) > 1:
            draw.line(pixels, fill='black', width=stroke_px, joint='curve')
        for x, y in pixels:
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill='black')
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()
