#!/usr/bin/env python3
"""
AUTOPILOT — run this one file and it sets up the whole Shorts factory.

What it does by itself:
  1. installs missing Python packages
  2. checks/installs ffmpeg
  3. renders your first Short (seed 4821) as proof everything works
  4. opens the four Google Cloud pages you must click through (your account,
     your clicks — nobody, including software, should ever have your login)
  5. watches your Downloads folder and grabs client_secret*.json automatically
  6. runs the first upload (unlisted) — browser asks you to authorize once
  7. registers the daily schedule (2 uploads/day to start — safe volume)

If anything errors, copy the message and paste it to Claude.
"""

import glob
import os
import platform
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOWNLOADS = Path.home() / "Downloads"
QUEUE = HERE / "queue"


def step(msg):
    print(f"\n{'=' * 60}\n  {msg}\n{'=' * 60}")


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, **kw)


# ---------------------------------------------------------------- packages --
def ensure_packages():
    step("STEP 1/7 — Python packages")
    need = []
    for mod, pkg in [("numpy", "numpy"), ("PIL", "pillow"),
                     ("googleapiclient", "google-api-python-client"),
                     ("google_auth_oauthlib", "google-auth-oauthlib")]:
        try:
            __import__(mod)
        except ImportError:
            need.append(pkg)
    if not need:
        print("all packages already installed")
        return
    for extra in ([], ["--break-system-packages"], ["--user"]):
        r = run([sys.executable, "-m", "pip", "install", *need, *extra])
        if r.returncode == 0:
            return
    sys.exit("pip install failed — paste the output above to Claude.")


# ----------------------------------------------------------------- ffmpeg --
def ensure_ffmpeg():
    step("STEP 2/7 — ffmpeg")
    if shutil.which("ffmpeg"):
        print("ffmpeg found")
        return
    osname = platform.system()
    if osname == "Windows":
        run(["winget", "install", "-e", "--id", "Gyan.FFmpeg",
             "--accept-source-agreements", "--accept-package-agreements"])
        print("\nWindows only refreshes PATH in NEW windows.")
        sys.exit("ffmpeg installed — close this window and run install.bat "
                 "again to continue.")
    elif osname == "Darwin":
        run(["brew", "install", "ffmpeg"])
    else:
        run(["sudo", "apt-get", "install", "-y", "ffmpeg"])
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg still not on PATH — open a new terminal, run again.")


# ------------------------------------------------------------ test render --
def test_render():
    step("STEP 3/7 — rendering your first Short (seed 4821, 1-3 min)")
    mp4 = QUEUE / "escape_4821.mp4"
    if mp4.exists():
        print("already rendered:", mp4)
        return mp4
    run([sys.executable, str(HERE / "generate_short.py"),
         "--seed", "4821", "--outdir", str(QUEUE)], check=True)
    print("\nrendered:", mp4, "\n(open it and watch it — it's your video!)")
    return mp4


# ------------------------------------------------------------ google setup --
CONSOLE_STEPS = [
    ("Create a project (any name, e.g. shorts-factory)",
     "https://console.cloud.google.com/projectcreate"),
    ("Click ENABLE on the YouTube Data API v3",
     "https://console.cloud.google.com/apis/library/youtube.googleapis.com"),
    ("OAuth consent screen: choose External, fill the 2 required fields,\n"
     "      then under Test users ADD YOUR OWN GMAIL",
     "https://console.cloud.google.com/apis/credentials/consent"),
    ("Create credentials -> OAuth client ID -> type: Desktop app\n"
     "      -> then click DOWNLOAD JSON",
     "https://console.cloud.google.com/apis/credentials"),
]


def google_setup():
    step("STEP 4/7 — Google API access (the only part that needs your clicks)")
    if (HERE / "client_secret.json").exists():
        print("client_secret.json already here — skipping")
        return
    print("I'll open each page. Do the action in the browser, come back,")
    print("press Enter for the next one. Your Google login stays yours —")
    print("never type it anywhere except google.com itself.")
    for i, (what, url) in enumerate(CONSOLE_STEPS, 1):
        input(f"\n  [{i}/4] {what}\n        press Enter to open the page... ")
        webbrowser.open(url)

    step("STEP 5/7 — watching for the downloaded JSON")
    print("Finish the download; I'll detect client_secret*.json in your")
    print("Downloads (or this folder) and move it into place automatically.")
    deadline = time.time() + 900
    while time.time() < deadline:
        hits = (list(HERE.glob("client_secret*.json"))
                + list(DOWNLOADS.glob("client_secret*.json")))
        hits = [h for h in hits if h.name != "client_secret.json"] \
            or [h for h in hits]
        if hits:
            src = max(hits, key=lambda p: p.stat().st_mtime)
            dst = HERE / "client_secret.json"
            if src.resolve() != dst.resolve():
                shutil.copy(src, dst)
            print("got it:", src.name)
            return
        time.sleep(2)
    sys.exit("Timed out waiting for the JSON — download it, then rerun me.")


# ------------------------------------------------------------ first upload --
def first_upload(mp4):
    step("STEP 6/7 — first upload (unlisted)")
    print("A browser tab will ask you to authorize the app once.")
    print("Because your app is 'unverified', click Advanced -> Continue —")
    print("it's YOUR app on YOUR account, this is expected.")
    run([sys.executable, str(HERE / "upload_youtube.py"),
         str(mp4), "--privacy", "unlisted"], check=True)
    print("\nNow check https://studio.youtube.com : the video should say")
    print("Unlisted. If it says 'locked private', request the API audit")
    print("(README section 2) — everything else still works meanwhile.")
    print("\nIMPORTANT for automation: on the OAuth consent screen page,")
    print("click PUBLISH APP (status: In production). In Testing mode,")
    print("Google expires your login token every 7 days and the schedule")
    print("would stop working weekly.")
    input("press Enter once you've checked... ")


# --------------------------------------------------------------- schedule --
def schedule():
    step("STEP 7/7 — daily automation")
    ans = input("Register 2 uploads/day (09:00 and 18:00)? Recommended "
                "starting volume. [Y/n] ").strip().lower()
    if ans == "n":
        print("Skipped. README section 4 shows how to do it later.")
        return
    py = sys.executable
    script = HERE / "daily_run.py"
    if platform.system() == "Windows":
        for name, t in [("ShortsFactoryAM", "09:00"),
                        ("ShortsFactoryPM", "18:00")]:
            run(["schtasks", "/Create", "/F", "/SC", "DAILY",
                 "/TN", name, "/ST", t,
                 "/TR", f'"{py}" "{script}" --count 1'])
        print("\nDone. Verify in Task Scheduler; if a task misfires, edit its")
        print(f'Action to: {py} "{script}" --count 1')
    else:
        lines = (f'0 9 * * * cd "{HERE}" && "{py}" daily_run.py --count 1\n'
                 f'0 18 * * * cd "{HERE}" && "{py}" daily_run.py --count 1\n')
        cur = subprocess.run(["crontab", "-l"], capture_output=True,
                             text=True).stdout
        if "daily_run.py" in cur:
            print("cron entries already present")
        else:
            subprocess.run(["crontab", "-"], input=cur + lines, text=True)
            print("cron installed — verify with: crontab -l")
    print("\nNote: your laptop must be ON at those times. If it often")
    print("isn't, tell Claude — a $5/mo cloud VM or a spare PC solves it.")


# ------------------------------------------------------------------- main --
def main():
    print(__doc__)
    os.chdir(HERE)
    QUEUE.mkdir(exist_ok=True)
    ensure_packages()
    ensure_ffmpeg()
    mp4 = test_render()
    google_setup()
    first_upload(mp4)
    schedule()
    step("ALL DONE")
    print("Factory is live. Useful commands:")
    print("  python daily_run.py --count 1              one video now")
    print("  python daily_run.py --count 3 --no-upload  batch to review")
    print("  python upload_youtube.py --dir queue --archive published")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped — run me again anytime, I skip finished steps.")
    except subprocess.CalledProcessError as e:
        print(f"\nA command failed (exit {e.returncode}). Paste everything "
              "above to Claude and it'll be fixed.")
