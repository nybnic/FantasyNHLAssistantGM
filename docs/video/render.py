"""The Assistant GM explainer video (docs/video/assistant-gm.mp4): video.html is a
GSAP timeline, seek(t) draws any moment, and this renders it frame by frame in
headless Chromium and encodes it with ffmpeg. Vertical 1080x1920, 120 s, no sound.

    python docs/video/render.py                          # -> data/charts/video.mp4 (~17 min)
    python docs/video/render.py --stills 12,70,90        # PNGs to check a change -> data/charts/video-stills/
    python docs/video/render.py --start 64 --end 94      # one part

Needs Playwright's Chromium (or CHROMIUM_EXECUTABLE pointing at one), ffmpeg, and the
network for GSAP and the fonts (or gsap.min.js saved next to video.html). Open
video.html?play in a browser to watch it live, video.html?t=70 for one moment.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE.parent.parent / "data" / "charts"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stills", help="comma-separated seconds")
    ap.add_argument("--dir", type=Path, default=OUT / "video-stills")
    ap.add_argument("--out", type=Path, default=OUT / "video.mp4")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--scale", type=float, default=2, help="pixels per CSS pixel (the page is 540x960)")
    a = ap.parse_args()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_EXECUTABLE") or None)
        pg = b.new_page(viewport={"width": 540, "height": 960}, device_scale_factor=a.scale)
        pg.goto((HERE / "video.html").as_uri())
        pg.evaluate("window.ready")
        pg.wait_for_timeout(300)
        if a.stills:
            a.dir.mkdir(parents=True, exist_ok=True)
            for t in a.stills.split(","):
                pg.evaluate(f"seek({float(t)})")
                pg.screenshot(path=str(a.dir / f"t{float(t):05.1f}.png"))
            return
        end = a.end if a.end is not None else pg.evaluate("DURATION")
        n = int(round((end - a.start) * a.fps))
        w, h = int(540 * a.scale), int(960 * a.scale)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(a.fps),
                               "-i", "-", "-vf", f"scale={w}:{h}", "-c:v", "libx264", "-preset", "slow", "-crf", "25",
                               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(a.out)], stdin=subprocess.PIPE)
        for i in range(n):
            pg.evaluate(f"seek({a.start + i / a.fps})")
            ff.stdin.write(pg.screenshot(type="jpeg", quality=92))
            if i % 300 == 0:
                print(f"{i}/{n}", file=sys.stderr, flush=True)
        ff.stdin.close()
        ff.wait()


if __name__ == "__main__":
    main()
