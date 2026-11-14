"""Regenerate brand exports from icon.svg. Requires Pillow for authoring only."""

from pathlib import Path
import defusedxml.ElementTree as ET

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / 'agentops' / 'brand'


def icon(size):
    scale = size * 4 / 32
    image = Image.new('RGBA', (size * 4, size * 4))
    draw = ImageDraw.Draw(image)
    for rect in ET.parse(BRAND / 'icon.svg').getroot():
        if rect.tag.rsplit('}', 1)[-1] != 'rect':
            continue
        x, y = float(rect.get('x', 0)), float(rect.get('y', 0))
        w, h = float(rect.get('width')), float(rect.get('height'))
        box = tuple(round(v * scale) for v in (x, y, x + w, y + h))
        draw.rounded_rectangle(box, radius=round(float(rect.get('rx', 0)) * scale),
                               fill=rect.get('fill'))
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main():
    master = icon(256)
    master.save(BRAND / 'icon.ico', sizes=[(n, n) for n in (16, 20, 24, 32, 48, 64, 128, 256)])
    for size in (32, 180, 192, 512):
        icon(size).save(BRAND / f'icon-{size}.png')
    mark = (BRAND / 'icon.svg').read_text(encoding='utf8')
    inner = mark[mark.index('>') + 1:mark.rindex('</svg>')]
    (BRAND / 'repository.svg').write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="280" viewBox="0 0 1080 280">'
        '<title>Headroom AgentOps — Room to ship.</title>'
        '<rect width="1080" height="280" rx="24" fill="#102733"/>'
        '<path d="M780 0V280M900 0V280M1020 0V280" stroke="#1e3a46"/>'
        '<svg x="52" y="52" width="176" height="176" viewBox="0 0 32 32">' + inner + '</svg>'
        '<text x="270" y="119" fill="#f5f4ed" font-family="Segoe UI,Arial,sans-serif" '
        'font-size="62" font-weight="650" letter-spacing="-2">Headroom</text>'
        '<text x="274" y="166" fill="#73e2c0" font-family="Segoe UI,Arial,sans-serif" '
        'font-size="26" letter-spacing="3">AgentOps</text>'
        '<text x="274" y="215" fill="#b4c6cc" font-family="Segoe UI,Arial,sans-serif" '
        'font-size="20">Room to ship.</text></svg>\n', encoding='utf8', newline='\n')


if __name__ == '__main__':
    main()
