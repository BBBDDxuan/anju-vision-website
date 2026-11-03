"""Render model-cited original frames for visual review, with unaltered source timestamps."""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 16)
for memory_path in (ROOT / 'data/validation').glob('*-memory.json'):
    doc = json.loads(memory_path.read_text(encoding='utf8'))
    run = doc['job']['id']
    points = [0, 3000, 4000, 5000, 7000, 12000] if memory_path.name.startswith('cabinet') else [7000, 9000, 10000, 11000, 13000, 23000]
    sheet = Image.new('RGB', (960, 960), '#f4f3ec')
    draw = ImageDraw.Draw(sheet)
    for index, point in enumerate(points):
        frame = min(doc['frames'], key=lambda f: abs(f['pts_ms'] - point))
        image = Image.open(ROOT / 'data/runs' / run / 'frames' / (frame['id']+'.jpg'))
        image.thumbnail((470, 270))
        x,y = (index%2)*480, (index//2)*320
        sheet.paste(image, (x,y))
        draw.text((x+8,y+275), f"clip {frame['pts_ms']/1000:.1f}s | frame {frame['id']}", font=font, fill='#183c36')
    sheet.save(ROOT / 'evidence' / (memory_path.stem + '-review.jpg'))
