#!/usr/bin/env python3
"""
One-time YouTube authorization WITHOUT a browser on this machine.

Uses Google's OAuth 2.0 Device Authorization Grant ("TV and Limited Input
devices"): this script prints a short code, you open google.com/device on
your PHONE, type the code, pick the channel and approve. The refresh token
comes back here — no PC, no localhost redirect, no browser on the runner.

Requires an OAuth client of type **TV and Limited Input devices**.
A "Desktop app" client will NOT work with this flow.

Env in:
  YT_CLIENT_ID, YT_CLIENT_SECRET
Env out (written to $GITHUB_OUTPUT when running in Actions):
  refresh_token

Local use:
  YT_CLIENT_ID=... YT_CLIENT_SECRET=... python3 tools/device_auth.py
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEVICE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def post(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        payload = e.read().decode()
        try:
            return None, json.loads(payload)
        except json.JSONDecodeError:
            return None, {"error": f"http_{e.code}", "raw": payload[:400]}


def main():
    cid = os.environ.get("YT_CLIENT_ID", "").strip()
    csec = os.environ.get("YT_CLIENT_SECRET", "").strip()
    if not cid or not csec:
        sys.exit("Set YT_CLIENT_ID and YT_CLIENT_SECRET first.")

    start, err = post(DEVICE_URL, {"client_id": cid, "scope": SCOPE})
    if err:
        sys.exit(f"Could not start device flow: {err}\n"
                 "Most common cause: the OAuth client is a 'Desktop app'. "
                 "It must be created as 'TV and Limited Input devices'.")

    url = start.get("verification_url") or start.get("verification_uri")
    print("\n" + "=" * 58)
    print("  OPEN THIS ON YOUR PHONE:   " + url)
    print("  ENTER THIS CODE:           " + start["user_code"])
    print("=" * 58)
    print("\n  Sign in with the Google account that owns the KIDS channel.")
    print("  If you have several channels, pick the kids one on the")
    print("  account-chooser screen. Then approve the upload permission.\n")
    sys.stdout.flush()

    interval = int(start.get("interval", 5))
    deadline = time.time() + int(start.get("expires_in", 1800))
    creds = None
    while time.time() < deadline:
        time.sleep(interval)
        creds, err = post(TOKEN_URL, {
            "client_id": cid,
            "client_secret": csec,
            "device_code": start["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        if creds:
            break
        code = (err or {}).get("error")
        if code == "authorization_pending":
            continue
        if code == "slow_down":
            interval += 5
            continue
        if code == "expired_token":
            sys.exit("The code expired. Re-run this step and be quicker.")
        if code == "access_denied":
            sys.exit("You denied the request on the phone.")
        sys.exit(f"Token exchange failed: {err}")

    if not creds:
        sys.exit("Timed out waiting for you to approve on the phone.")

    rt = creds.get("refresh_token")
    if not rt:
        sys.exit("Google returned no refresh_token. Revoke the app's access "
                 "at myaccount.google.com/permissions and try again.")

    print("Authorized. Refresh token acquired.")

    # Hand the token to the workflow WITHOUT printing it into the logs.
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"refresh_token={rt}\n")
        print("Refresh token written to the step output (masked in logs).")
    else:
        print("\nStore this as the GitHub secret YT_REFRESH_TOKEN:\n")
        print(rt)


if __name__ == "__main__":
    main()
