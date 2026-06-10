"""Send a fixture payload to a running Meridian instance.

Computes the correct signature so you never hand-roll the HMAC in a shell.

Usage (server must be running on :8000):
    python backend/tests/fixtures/send_fixture.py github_ci_failure.json
    python backend/tests/fixtures/send_fixture.py gitlab_pipeline.json --gitlab
"""

import argparse
import hashlib
import hmac
import sys
from pathlib import Path

import httpx

from backend.config import settings

FIXTURE_DIR = Path(__file__).resolve().parent

# Map fixture filenames to the GitHub event header they represent.
GITHUB_EVENT_BY_FILE = {
    "github_push.json": "push",
    "github_ci_failure.json": "check_run",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="POST a fixture to a webhook endpoint")
    parser.add_argument("fixture", help="fixture filename under tests/fixtures/")
    parser.add_argument("--gitlab", action="store_true", help="send to the GitLab endpoint")
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    body = (FIXTURE_DIR / args.fixture).read_bytes()

    if args.gitlab:
        url = f"{args.base_url}/webhooks/gitlab"
        headers = {
            "Content-Type": "application/json",
            "X-Gitlab-Token": settings.GITLAB_WEBHOOK_SECRET,
            "X-Gitlab-Event": "Pipeline Hook",
        }
    else:
        signature = "sha256=" + hmac.new(
            settings.GITHUB_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        url = f"{args.base_url}/webhooks/github"
        headers = {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": GITHUB_EVENT_BY_FILE.get(args.fixture, "push"),
        }

    resp = httpx.post(url, content=body, headers=headers, timeout=30.0)
    print(f"{resp.status_code} {resp.text}")
    return 0 if resp.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
