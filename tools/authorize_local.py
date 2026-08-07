#!/usr/bin/env python3
"""
Authorize a YouTube channel from a PC, and store the result as GitHub
secrets. This is the path that actually works reliably.

google-auth-oauthlib starts a real local web server, so Google's redirect
to http://localhost:PORT resolves properly and the authorization code is
captured automatically. Nothing to copy out of an address bar. The phone
alternatives (loopback on a device with no server, or the device-code flow)
both need workarounds this does not.

It refuses to store anything until it has proved two things:
  1. the refresh token actually mints an access token
  2. it belongs to the channel you expected
Getting (2) wrong publishes kids videos to the wrong channel, which is
worse than failing.

Usage:
  python tools/authorize_local.py --client path/to/client_secret.json
  python tools/authorize_local.py --client c.json --expect "Cloudtop Kids"
  python tools/authorize_local.py --client c.json --no-store   # dry run

Requires an OAuth client of type "Desktop app", and the consent screen
PUBLISHED (in Testing mode Google expires refresh tokens after 7 days).
"""

import argparse
import subprocess
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

DEFAULT_REPO = "RingEscapeSims/shorts-factory"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # readonly is requested only so the channel can be confirmed by name
    # before anything is stored. Nothing writes except videos.insert.
    "https://www.googleapis.com/auth/youtube.readonly",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True,
                    help="client_secret.json downloaded from Google Cloud")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--expect", default=None,
                    help="channel title to require, e.g. 'Cloudtop Kids'")
    ap.add_argument("--no-store", action="store_true",
                    help="verify only; do not write GitHub secrets")
    args = ap.parse_args()

    print("Opening your browser ...", flush=True)
    print("  Sign in as the account that owns the channel, pick the right",
          flush=True)
    print("  channel if asked, then click Continue / Allow.", flush=True)
    print(flush=True)

    flow = InstalledAppFlow.from_client_secrets_file(args.client, SCOPES)
    creds = flow.run_local_server(
        port=0, access_type="offline", prompt="consent", open_browser=True,
        authorization_prompt_message="",
        success_message="Done. Close this tab and go back to the chat.",
    )
    if not creds.refresh_token:
        sys.exit("No refresh token returned. Revoke this app at "
                 "myaccount.google.com/permissions and try again.")

    cid = flow.client_config["client_id"]
    csec = flow.client_config["client_secret"]

    print("verifying ...", flush=True)
    test = Credentials(token=None, refresh_token=creds.refresh_token,
                       client_id=cid, client_secret=csec,
                       token_uri="https://oauth2.googleapis.com/token")
    try:
        test.refresh(Request())
    except Exception as e:
        sys.exit(f"REFRESH FAILED, nothing stored: {e}")
    print("  token refreshes OK", flush=True)

    yt = build("youtube", "v3", credentials=test)
    items = yt.channels().list(part="snippet", mine=True).execute().get(
        "items", [])
    if not items:
        sys.exit("No channel returned; refusing to store.")
    title = items[0]["snippet"]["title"]
    print(f"  channel: {title}", flush=True)

    if args.expect and title.strip().lower() != args.expect.strip().lower():
        sys.exit(f"WRONG CHANNEL: got {title!r}, expected {args.expect!r}. "
                 "Nothing stored. Re-run and pick the right channel.")

    if args.no_store:
        print("\n--no-store given; verified but not written.", flush=True)
        return

    for name, val in (("YT_REFRESH_TOKEN", creds.refresh_token),
                      ("YT_CLIENT_ID", cid),
                      ("YT_CLIENT_SECRET", csec)):
        p = subprocess.run(["gh", "secret", "set", name, "--repo", args.repo],
                           input=val, text=True, capture_output=True)
        if p.returncode != 0:
            sys.exit(f"storing {name} failed: {p.stderr[:300]}")
        print(f"  stored {name}", flush=True)

    print(f"\nDONE - uploads will go to: {title}", flush=True)


if __name__ == "__main__":
    main()
