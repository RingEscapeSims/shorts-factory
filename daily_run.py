#!/usr/bin/env python3
"""
One command = one day of content. Schedule this with cron / Task Scheduler.

  python3 daily_run.py --count 2                 # generate 2, upload 2
  python3 daily_run.py --count 6 --spread 3600   # 6 videos, 1 upload per hour
  python3 daily_run.py --count 3 --no-upload     # generate only, review first

Files land in ./queue, then move to ./published after a successful upload,
so nothing is ever uploaded twice.
"""

import argparse
import glob
import os
import random
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, "queue")
PUBLISHED = os.path.join(HERE, "published")


def sh(cmd):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


ENGINES = {
    "rings": ["generate_short.py"],
    "kids": ["kids_studio.py", "--mode", "random"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=2)
    ap.add_argument("--engine", default="rings",
                    choices=["rings", "kids", "mix"],
                    help="which generator to run (mix alternates)")
    ap.add_argument("--spread", type=int, default=0,
                    help="seconds to wait between uploads (spacing looks organic)")
    ap.add_argument("--no-upload", action="store_true")
    args = ap.parse_args()

    os.makedirs(QUEUE, exist_ok=True)
    os.makedirs(PUBLISHED, exist_ok=True)

    for i in range(args.count):
        eng = args.engine if args.engine != "mix" \
            else ("kids" if i % 2 == 0 else "rings")
        seed = random.randrange(1, 10 ** 6)
        sh([sys.executable, os.path.join(HERE, ENGINES[eng][0]),
            *ENGINES[eng][1:], "--count", "1", "--seed", str(seed),
            "--outdir", QUEUE])

    if args.no_upload:
        print("Generated only. Review the queue folder, then run "
              "upload_youtube.py --dir queue --archive published")
        return

    videos = sorted(glob.glob(os.path.join(QUEUE, "*.mp4")))
    for i, mp4 in enumerate(videos):
        sh([sys.executable, os.path.join(HERE, "upload_youtube.py"),
            mp4, "--archive", PUBLISHED])
        if args.spread and i < len(videos) - 1:
            print(f"sleeping {args.spread}s before next upload...")
            time.sleep(args.spread)


if __name__ == "__main__":
    main()
