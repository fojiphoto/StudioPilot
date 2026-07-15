"""One-time Google OAuth helper - gets a refresh token WITHOUT sharing any password.

How it works: this opens Google's consent page in YOUR browser. You log in there
yourself (with your normal password + authenticator). Google then redirects back to
this script running on localhost with a one-time code, which is exchanged for a
refresh token. The token is printed here; paste it into .env.

Usage (from repo root, venv active):
    python scripts/google_oauth.py admob        # AdMob Mediation Report API
    python scripts/google_oauth.py googleads    # Google Ads API (Phase 2)

Reads ADMOB_CLIENT_ID / ADMOB_CLIENT_SECRET (or GOOGLE_ADS_*) from .env.
"""
from __future__ import annotations

import http.server
import os
import sys
import threading
import urllib.parse
import webbrowser

import httpx
from dotenv import load_dotenv

SCOPES = {
    "admob": ("ADMOB", "https://www.googleapis.com/auth/admob.readonly"),
    "googleads": ("GOOGLE_ADS", "https://www.googleapis.com/auth/adwords"),
}
PORT = 8765
REDIRECT_URI = f"http://localhost:{PORT}"

_auth_code: dict = {}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _auth_code["code"] = query.get("code", [None])[0]
        _auth_code["error"] = query.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<h2>Done - you can close this tab and go back to the terminal.</h2>".encode())

    def log_message(self, *args):  # silence request logging
        pass


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "admob"
    if target not in SCOPES:
        print(f"unknown target '{target}' - use one of: {', '.join(SCOPES)}")
        return 1
    env_prefix, scope = SCOPES[target]

    load_dotenv()
    client_id = os.getenv(f"{env_prefix}_CLIENT_ID", "")
    client_secret = os.getenv(f"{env_prefix}_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print(f"{env_prefix}_CLIENT_ID / {env_prefix}_CLIENT_SECRET missing from .env - add them first.")
        return 1

    server = http.server.HTTPServer(("localhost", PORT), _Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": scope,
            "access_type": "offline",   # this is what yields a refresh token
            "prompt": "consent",
        }
    )
    print("Opening your browser for Google login/consent...")
    print("(If it doesn't open, paste this URL yourself:)\n" + auth_url + "\n")
    webbrowser.open(auth_url)

    print("Waiting for Google to redirect back (login + approve in the browser)...")
    server_thread_done = threading.Event()

    # handle_request already ran in the thread; wait for the code to land
    import time

    for _ in range(600):  # up to 10 minutes
        if _auth_code:
            break
        time.sleep(1)

    if _auth_code.get("error") or not _auth_code.get("code"):
        print(f"authorization failed: {_auth_code.get('error') or 'no code received (timeout?)'}")
        return 1

    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": _auth_code["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    payload = response.json()
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        print(f"no refresh token in response: {payload}")
        return 1

    print("\nSuccess! Add this line to your .env:\n")
    print(f"{env_prefix}_REFRESH_TOKEN={refresh_token}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
