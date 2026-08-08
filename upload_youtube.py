#!/usr/bin/env python3
"""
Upload a generated Short to YouTube via the official Data API v3 (free).

One-time setup (see README.md):
  1. Google Cloud Console -> new project -> enable "YouTube Data API v3"
  2. OAuth consent screen -> External -> add your own Google account as test user
  3. Credentials -> OAuth client ID -> Desktop app -> download client_secret.json
     into this folder
  4. pip install google-api-python-client google-auth-oauthlib
  5. First run opens a browser once to authorize; token.json is cached after.

Usage:
  python3 upload_youtube.py out/escape_4821.mp4
  python3 upload_youtube.py out/escape_4821.mp4 --privacy unlisted
  python3 upload_youtube.py --dir out            # upload every .mp4 in a folder

Quota note: each upload costs 1,600 units of the default 10,000/day quota,
so the API allows ~6 uploads per day out of the box.
"""

import argparse
import glob
import json
import os
import sys
import time

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError, ResumableUploadError
    from googleapiclient.http import MediaFileUpload
except ImportError:
    sys.exit("Missing packages. Run:\n"
             "  pip install google-api-python-client google-auth-oauthlib")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET = os.path.join(HERE, "client_secret.json")
TOKEN_FILE = os.path.join(HERE, "token.json")


def _creds_from_env():
    """Headless auth for CI: rebuild credentials from three env vars.

    Set YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN (see
    tools/device_auth.py for how to obtain the refresh token from a phone).
    Returns None if they aren't all present, so local runs fall through to
    the normal browser flow.
    """
    cid = os.environ.get("YT_CLIENT_ID", "").strip()
    csec = os.environ.get("YT_CLIENT_SECRET", "").strip()
    rtok = os.environ.get("YT_REFRESH_TOKEN", "").strip()
    if not (cid and csec and rtok):
        return None
    creds = Credentials(
        token=None,
        refresh_token=rtok,
        client_id=cid,
        client_secret=csec,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    creds.refresh(Request())          # exchange refresh -> access token now,
    return creds                      # so auth errors surface before upload


def get_service():
    """Three auth paths, tried in order:

    1. YT_CLIENT_ID/SECRET/REFRESH_TOKEN env vars  (headless, no files)
    2. token.json on disk  (what the original workflow writes from the
       TOKEN_JSON secret — still fully supported, do not break it)
    3. interactive browser flow  (local machines only)
    """
    creds = _creds_from_env()
    if creds is not None:
        print("auth: refresh token from environment (headless)")
        return build("youtube", "v3", credentials=creds)

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        print("auth: token.json")
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # No usable cached token. A browser flow is impossible on a
            # CI runner, so fail with a message that names both fixes.
            if os.environ.get("CI"):
                sys.exit(
                    "No usable credentials in CI. Provide EITHER "
                    "YT_CLIENT_ID + YT_CLIENT_SECRET + YT_REFRESH_TOKEN as "
                    "env vars (see SETUP_PHONE.md), OR a valid token.json "
                    "written from the TOKEN_JSON secret.")
            if not os.path.exists(CLIENT_SECRET):
                sys.exit(f"client_secret.json not found in {HERE} — "
                         "follow the setup steps in README.md")
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as fh:
            fh.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


# This check is deliberately INVERTED: rather than listing the kids
# engines, it lists the only general-audience one. Everything else is
# assumed child-directed and must declare selfDeclaredMadeForKids=true.
#
# The whitelist version rotted twice — a new lesson mode shipped, its
# prefix was not added here, and those videos would have gone up as general
# audience. Failing safe means a new mode I forget about gets REFUSED
# (annoying, obvious, fixable) instead of mislabelled (a COPPA problem).
# escape_*    generate_short.py       (rings Shorts)
# ringslong_* make_rings_long.py     (rings chaptered long-form)
GENERAL_PREFIXES = ("escape_", "ringslong_")
GENERAL_MODES = {"rings", "escape", "longform"}


def _is_general_audience(name, meta):
    if meta is not None and meta.get("mode") in GENERAL_MODES:
        return True
    return name.startswith(GENERAL_PREFIXES)


def load_meta(mp4_path):
    jpath = os.path.splitext(mp4_path)[0] + ".json"
    name = os.path.splitext(os.path.basename(mp4_path))[0]

    size = os.path.getsize(mp4_path)
    if size < 1024:
        sys.exit(f"REFUSING to upload {name}: the file is {size} bytes, "
                 "which means the render was interrupted. Delete it and "
                 "re-render.")

    if os.path.exists(jpath):
        with open(jpath) as fh:
            meta = json.load(fh)
        if not _is_general_audience(name, meta) \
                and not meta.get("selfDeclaredMadeForKids"):
            sys.exit(f"REFUSING to upload {name}: it is not from the "
                     "general-audience rings engine, so it is treated as "
                     "child-directed, but its metadata does not declare "
                     "selfDeclaredMadeForKids=true (COPPA requirement). If "
                     "it really is general-audience, add its prefix to "
                     "GENERAL_PREFIXES.")
        return meta

    if not _is_general_audience(name, None):
        sys.exit(f"REFUSING to upload {name}: metadata sidecar {jpath} is "
                 "missing, and guessing the Made-for-Kids flag on a "
                 "possibly child-directed video is not safe. Re-render it.")
    return dict(title=name, description="#shorts", tags=["shorts"],
                categoryId="24", privacyStatus="public",
                selfDeclaredMadeForKids=False)


def upload(youtube, mp4_path, privacy_override=None):
    meta = load_meta(mp4_path)
    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta.get("tags", []),
            "categoryId": meta.get("categoryId", "24"),
        },
        "status": {
            "privacyStatus": privacy_override or meta.get("privacyStatus", "public"),
            "selfDeclaredMadeForKids": meta.get("selfDeclaredMadeForKids", False),
        },
    }
    media = MediaFileUpload(mp4_path, chunksize=4 * 1024 * 1024,
                            resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media)

    print(f"Uploading {os.path.basename(mp4_path)} ...")
    response = None
    backoff = 2
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"  {int(status.progress() * 100)}%")
            backoff = 2
        except (HttpError, ResumableUploadError) as err:
            code = getattr(err, "resp", None)
            code = getattr(code, "status", None)
            if code in (500, 502, 503, 504):
                print(f"  transient {code}, retrying in {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
            else:
                raise
    vid = response["id"]
    print(f"  done -> https://youtube.com/shorts/{vid}")
    return vid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", nargs="?", help="path to one .mp4")
    ap.add_argument("--dir", help="upload every .mp4 in this folder")
    ap.add_argument("--privacy", choices=["public", "unlisted", "private"])
    ap.add_argument("--archive", default=None,
                    help="move uploaded files into this folder")
    args = ap.parse_args()

    targets = []
    if args.video:
        targets = [args.video]
    elif args.dir:
        targets = sorted(glob.glob(os.path.join(args.dir, "*.mp4")))
    if not targets:
        sys.exit("Nothing to upload. Pass a file or --dir folder.")

    # Validate every sidecar BEFORE touching the network, so a mislabelled
    # or missing-metadata video fails fast instead of half-way through a batch.
    for path in targets:
        load_meta(path)
    print(f"{len(targets)} file(s) passed metadata checks.")

    yt = get_service()
    for path in targets:
        upload(yt, path, args.privacy)
        if args.archive:
            os.makedirs(args.archive, exist_ok=True)
            base = os.path.splitext(path)[0]
            for ext in (".mp4", ".json"):
                if os.path.exists(base + ext):
                    os.rename(base + ext,
                              os.path.join(args.archive,
                                           os.path.basename(base) + ext))


if __name__ == "__main__":
    main()
