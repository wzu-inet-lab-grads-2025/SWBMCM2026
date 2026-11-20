"""Local frames for the two training examples selected by c_palm_motion.

AI-assisted data inspection, OpenCode / OpenAI GPT family, GPT-6.2 Astra
routing group, 2026-11-20. Exact release date unavailable.
"""

import csv
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw


def sheet(directory):
    with (directory/'examples_private.csv').open() as handle:
        samples = list(csv.DictReader(handle))
    if not samples:
        raise ValueError('No training examples met the diagnostic criteria')
    canvas = Image.new('RGB', (3*440, 365*len(samples)), 'white')
    draw = ImageDraw.Draw(canvas)
    with TemporaryDirectory(dir=directory) as tmp:
        for row, sample in enumerate(samples):
            videos = Path(sample['folder'])/'videos'
            with (videos/'head_left_frames.csv').open(encoding='utf-8-sig') as handle:
                stamps = [int(r['video_timestamp_ns']) for r in csv.DictReader(handle)]
            start_ns, peak_ns = int(sample['anchor_ns']), int(sample['peak_ns'])
            if len(set(stamps)) != len(stamps) or stamps != sorted(stamps):
                raise ValueError('Video sidecar clock is not unique and ordered')
            start, peak = stamps.index(start_ns), stamps.index(peak_ns)
            ids = [start, (start+peak)//2, peak]
            if len(set(ids)) != 3:
                raise ValueError('Example needs three distinct frames')
            video = str(videos/'head_left.mp4')
            count = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                             '-show_entries', 'stream=nb_frames', '-of', 'csv=p=0', video], text=True)
            if int(count.strip()) != len(stamps):
                raise ValueError('Presentation frame count and sidecar disagree')
            rule = '+'.join(f'eq(n\\,{i})' for i in ids)
            subprocess.run(['ffmpeg', '-v', 'error', '-i', video, '-vf', f'select={rule},scale=440:-2',
                            '-vsync', '0', '-frames:v', '3', f'{tmp}/{row}_%d.png'], check=True)
            y = row*365
            draw.text((5, y+2), f"{sample['dataset']}  {sample['record']}  {sample['side']} hand   |   head-left view", fill='black')
            for col, i in enumerate(ids):
                with Image.open(f'{tmp}/{row}_{col+1}.png') as image:
                    image.thumbnail((440, 320))
                    canvas.paste(image, (col*440, y+22))
                draw.text((col*440+5, y+342), f'{(stamps[i]-start_ns)/1e9:.3f} s after anchor', fill='black')
    canvas.save(directory/'frames_private.jpg', quality=92)


if __name__ == '__main__':
    sheet(Path('runs/c_palm_motion'))
