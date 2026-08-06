#!/usr/bin/env python3
"""
SIMPLE AUTH — authorize a second YouTube channel WITHOUT touching the
Google Cloud console.

It reuses the OAuth client you already created for the rings channel (the
one stored in the CLIENT_SECRET_JSON secret). An OAuth client identifies
the APPLICATION, not the user, so the same client can be approved by a
different Google account to reach a different channel.

Two steps, both run as GitHub Actions with no PC involved:

  step 1  ->  prints a Google link. You open it on your phone, sign in as
              the KIDS account, pick the kids channel, approve. Google then
              tries to send you to http://localhost:1/?code=XXXX which will
              NOT load ("site can't be reached") — that is expected and
              correct. Copy the code out of the address bar.

  step 2  ->  you paste that code in; this exchanges it for a refresh token
              and stores it as the YT_REFRESH_TOKEN secret.

Why the broken page: a "Desktop app" OAuth client is only allowed to send
you back to localhost. There is no server on your phone, so the page fails,
but the authorization code is right there in the URL. That is the whole
trick — no console, no new client type.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
# Any localhost port is accepted for an installed/desktop client. Port 1 is
# used because nothing will ever be listening on it, so the browser fails
# fast and instantly instead of hanging.
REDIRECT = "http://localhost:1"


def load_client():
    """Read client id/secret from CLIENT_SECRET_JSON (the rings client)."""
    raw = os.environ.get("CLIENT_SECRET_JSON", "").strip()
    if not raw:
        sys.exit("CLIENT_SECRET_JSON is empty. That secret holds the OAuth "
                 "client you set up for the rings channel.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit("CLIENT_SECRET_JSON is not valid JSON. Re-copy the whole "
                 "file contents, including the outer { }.")
    node = data.get("installed") or data.get("web") or data
    cid, csec = node.get("client_id"), node.get("client_secret")
    if not cid or not csec:
        sys.exit("client_id / client_secret missing from CLIENT_SECRET_JSON.")
    return cid, csec


def step_link():
    cid, _ = load_client()
    params = {
        "client_id": cid,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",     # required to get a refresh token
        "prompt": "consent",          # force a refresh token even if the
                                      # account approved this app before
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)
    print()
    print("=" * 62)
    print("  1. OPEN THIS LINK ON YOUR PHONE")
    print("=" * 62)
    print()
    print(url)
    print()
    print("=" * 62)
    print("  2. Sign in as the KIDS Google account.")
    print("     If a channel chooser appears, pick the KIDS channel.")
    print("     If you see 'Google hasn't verified this app', tap")
    print("     Advanced -> Go to ... (unsafe). It is your own app.")
    print()
    print("  3. After you approve, the browser will try to open")
    print("     http://localhost:1/?code=...  and will FAIL to load.")
    print("     THAT IS CORRECT. Do not close it.")
    print()
    print("  4. Copy the long code from the address bar: everything")
    print("     between  code=  and  &scope  (it starts with 4/ ).")
    print()
    print("  5. Run this same workflow again, choose step 'exchange',")
    print("     and paste that code into the box.")
    print("=" * 62)


def step_exchange(code):
    cid, csec = load_client()
    code = urllib.parse.unquote(code.strip())
    if code.startswith("http"):                 # they pasted the whole URL
        q = urllib.parse.urlparse(code).query
        got = urllib.parse.parse_qs(q).get("code")
        if not got:
            sys.exit("That URL has no code= in it. Copy the address bar "
                     "from the page that failed to load.")
        code = got[0]
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": cid,
        "client_secret": csec,
        "redirect_uri": REDIRECT,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400]
        hint = ""
        if "invalid_grant" in detail:
            hint = ("\n\nThis almost always means the code was already used "
                    "or is more than a few minutes old. Run step 'link' "
                    "again and be quicker.")
        sys.exit(f"Token exchange failed: {detail}{hint}")

    rt = tok.get("refresh_token")
    if not rt:
        sys.exit("Google returned no refresh_token. Revoke this app at "
                 "myaccount.google.com/permissions on the kids account, "
                 "then run step 'link' again.")
    print("Authorized. Refresh token acquired.")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"refresh_token={rt}\n")
        print("Token handed to the workflow (never printed to this log).")
    else:
        print("\nStore this as the GitHub secret YT_REFRESH_TOKEN:\n")
        print(rt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["link", "exchange"])
    ap.add_argument("--code", default="")
    args = ap.parse_args()
    if args.step == "link":
        step_link()
    else:
        if not args.code.strip():
            sys.exit("No code supplied. Run the 'link' step first.")
        step_exchange(args.code)


if __name__ == "__main__":
    main()
