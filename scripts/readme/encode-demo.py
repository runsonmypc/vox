"""Encode captured RGBA frames as an APNG, preserving native transparency losslessly."""
import json
import sys
from pathlib import Path

from PIL import Image

source, destination = map(Path, sys.argv[1:])
manifest = json.loads((source / 'frames.json').read_text())
frames = [Image.open(source / row['file']).convert('RGBA') for row in manifest]
durations = [row['duration'] for row in manifest]
frames[0].save(destination, save_all=True, append_images=frames[1:], duration=durations,
               loop=0, disposal=0, blend=0, optimize=True)
with Image.open(destination) as animation:
    elapsed = 0
    source_index = 0
    for i in range(animation.n_frames):
        animation.seek(i)
        frame = animation.convert('RGBA')
        duration = round(animation.info['duration'])
        covered = 0
        while covered < duration:
            assert frame.tobytes() == frames[source_index].tobytes(), f'Changed pixels in frame {i}'
            covered += durations[source_index]
            source_index += 1
        assert covered == duration
        alpha = frame.getchannel('A')
        assert alpha.getpixel((0, 0)) == 0
        assert any(0 < a < 255 for a in alpha.tobytes())
        elapsed += duration
    assert elapsed == sum(durations) and source_index == len(frames)
    print(f'{animation.n_frames} RGBA frames, {elapsed / 1000}s, {destination.stat().st_size:,} bytes; lossless pixels and alpha verified')
Image.open(source / 'snippets-delivered.png').save(destination.with_name('app-demo-still.png'), optimize=True)
