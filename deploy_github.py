#!/usr/bin/env python3
"""One-command deploy to GitHub Actions (free, always-on, no server).

It does every step for you:
  1. creates the repository
  2. grants the workflow write permission (needed to commit results history)
  3. uploads BOT_TOKEN / CHAT_IDS / APIFOOTBALL_KEY as encrypted Actions secrets
  4. pushes the code
  5. triggers the workflow once so you get a Telegram message immediately

Usage:
    python deploy_github.py --token ghp_xxx
    python deploy_github.py --token ghp_xxx --repo my-bot --public
    python deploy_github.py --token ghp_xxx --dry-run      # show plan, do nothing

Get a token at https://github.com/settings/tokens/new
  - "Generate new token (classic)"
  - Expiration: 7 days is plenty
  - Tick ONLY the `repo` and `workflow` scopes
  - Delete it afterwards; the bot never needs it again.

Secrets are encrypted locally with libsodium before upload — GitHub never
receives them in plain text, and they are never written into a commit.
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"
API = "https://api.github.com"
SECRET_KEYS = ["BOT_TOKEN", "CHAT_IDS", "APIFOOTBALL_KEY"]


def c(txt: str, code: str) -> str:
    return f"\033[{code}m{txt}\033[0m"


ok, bad, info = c("✅", "32"), c("❌", "31"), "  "


def read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV.exists():
        return out
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if check and p.returncode != 0:
        print(f"{bad} command failed: {' '.join(cmd)}")
        print((p.stderr or p.stdout).strip()[:1500])
        sys.exit(1)
    return p


def encrypt(public_key_b64: str, value: str) -> str:
    try:
        from nacl import encoding, public
    except ImportError:
        print(f"{bad} this script needs PyNaCl to encrypt secrets:")
        print("     pip install pynacl")
        sys.exit(1)
    pk = public.PublicKey(public_key_b64.encode(), encoding.Base64Encoder())
    sealed = public.SealedBox(pk).encrypt(value.encode())
    return base64.b64encode(sealed).decode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.getenv("GITHUB_TOKEN", ""),
                    help="GitHub personal access token (repo + workflow scopes)")
    ap.add_argument("--repo", default="football-bot", help="repository name")
    ap.add_argument("--public", action="store_true",
                    help="public repo = unlimited free Actions minutes "
                         "(private = 2000 min/month, also plenty)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    env = read_env()
    missing = [k for k in ("BOT_TOKEN",) if not env.get(k)]
    if missing:
        print(f"{bad} {', '.join(missing)} missing from .env — run doctor.py first")
        return 1
    if not env.get("CHAT_IDS"):
        print(f"{bad} CHAT_IDS missing from .env — run `python link_chat.py` first")
        return 1

    print(c("\n🚀 Deploying football-bot to GitHub Actions\n", "1"))

    if a.dry_run:
        print(f"{info}would create repo : {a.repo} "
              f"({'public' if a.public else 'private'})")
        print(f"{info}would set secrets : "
              f"{', '.join(k for k in SECRET_KEYS if env.get(k))}")
        for k in SECRET_KEYS:
            v = env.get(k, "")
            shown = (v[:6] + "…") if v else c("(not set — optional)", "33")
            print(f"{info}  {k:18} {shown}")
        print(f"{info}would push        : branch main")
        print(f"{info}would trigger     : Daily football tickets")
        print(f"\n{ok} dry run only, nothing was sent")
        return 0

    if not a.token:
        print(f"{bad} no token. Pass --token ghp_xxx (see the header of this file)")
        return 1

    h = {"Authorization": f"Bearer {a.token}",
         "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    cl = httpx.Client(headers=h, timeout=45, follow_redirects=True)

    # 1 ----------------------------------------------------------- identity
    r = cl.get(f"{API}/user")
    if r.status_code != 200:
        print(f"{bad} token rejected ({r.status_code}): "
              f"{r.json().get('message', r.text[:200])}")
        print(f"{info}make sure you ticked the 'repo' and 'workflow' scopes")
        return 1
    user = r.json()["login"]
    scopes = r.headers.get("x-oauth-scopes", "")
    print(f"{ok} authenticated as {c(user, '1')}")
    if scopes and "repo" not in scopes:
        print(f"{bad} token lacks the 'repo' scope (has: {scopes or 'none'})")
        return 1

    owner, repo = user, a.repo
    full = f"{owner}/{repo}"

    # 2 --------------------------------------------------------- create repo
    r = cl.get(f"{API}/repos/{full}")
    if r.status_code == 200:
        print(f"{ok} repo {c(full, '1')} already exists — reusing it")
    else:
        r = cl.post(f"{API}/user/repos", json={
            "name": repo, "private": not a.public,
            "description": "Daily football accumulator bot — free data, "
                           "backtested engine, Telegram delivery",
            "has_issues": False, "has_wiki": False, "auto_init": False,
        })
        if r.status_code not in (200, 201):
            print(f"{bad} could not create repo: "
                  f"{r.json().get('message', r.text[:300])}")
            return 1
        print(f"{ok} created {c(full, '1')} "
              f"({'public' if a.public else 'private'})")

    # 3 ------------------------------------------- workflow write permission
    r = cl.put(f"{API}/repos/{full}/actions/permissions/workflow",
               json={"default_workflow_permissions": "write",
                     "can_approve_pull_request_reviews": False})
    if r.status_code in (204, 200):
        print(f"{ok} workflow granted write access (so it can commit results)")
    else:
        print(f"{info}⚠️  could not set workflow permissions automatically — "
              f"if the job fails on `git push`, set Settings → Actions → "
              f"General → Workflow permissions → Read and write")

    # 4 ---------------------------------------------------------- secrets --
    r = cl.get(f"{API}/repos/{full}/actions/secrets/public-key")
    if r.status_code != 200:
        print(f"{bad} could not fetch the secrets key: {r.text[:200]}")
        return 1
    key = r.json()

    for name in SECRET_KEYS:
        value = env.get(name, "")
        if not value:
            print(f"{info}– {name} not set locally, skipping (optional)")
            continue
        rr = cl.put(f"{API}/repos/{full}/actions/secrets/{name}", json={
            "encrypted_value": encrypt(key["key"], value),
            "key_id": key["key_id"],
        })
        if rr.status_code in (201, 204):
            print(f"{ok} secret {name} uploaded (encrypted)")
        else:
            print(f"{bad} secret {name} failed: {rr.text[:200]}")

    # 5 ------------------------------------------------------------- push --
    run(["git", "add", "-A"])
    run(["git", "commit", "-m", "deploy", "--allow-empty"], check=False)
    run(["git", "branch", "-M", "main"])
    remote = f"https://{a.token}@github.com/{full}.git"
    run(["git", "remote", "remove", "origin"], check=False)
    run(["git", "remote", "add", "origin", remote])
    p = run(["git", "push", "-u", "origin", "main", "--force"], check=False)
    if p.returncode != 0:
        print(f"{bad} push failed:\n{(p.stderr or p.stdout)[:800]}")
        return 1
    # don't leave the token sitting in .git/config
    run(["git", "remote", "set-url", "origin",
         f"https://github.com/{full}.git"], check=False)
    print(f"{ok} code pushed to main ({len(run(['git','ls-files']).stdout.splitlines())} files)")

    # 6 ---------------------------------------------------------- trigger --
    import time
    time.sleep(4)   # give GitHub a moment to register the workflow file
    r = cl.post(f"{API}/repos/{full}/actions/workflows/daily.yml/dispatches",
                json={"ref": "main"})
    if r.status_code == 204:
        print(f"{ok} workflow triggered — expect a Telegram message shortly")
    else:
        print(f"{info}⚠️  could not auto-trigger ({r.status_code}). "
              f"Run it by hand: Actions tab → Daily football tickets → Run workflow")

    print(c("\n🎉 Done.\n", "1;32"))
    print(f"{info}repo     https://github.com/{full}")
    print(f"{info}actions  https://github.com/{full}/actions")
    print(f"{info}secrets  https://github.com/{full}/settings/secrets/actions")
    print(f"\n{info}It now runs every day at 08:00 UTC (09:00 Tunis).")
    print(f"{info}Change the time in .github/workflows/daily.yml → cron.")
    print(c(f"\n{info}🔐 Now delete the deploy token: "
            f"https://github.com/settings/tokens\n", "33"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
