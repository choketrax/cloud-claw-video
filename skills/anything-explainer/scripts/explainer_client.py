#!/usr/bin/env python3
"""Anything2Explainer Studio Client for Cloud-Claw.

Connects to the remote Anything2Explainer service running at:
https://explainer-test-worker.dl-56e.workers.dev

Endpoints:
- POST /create: Start a new explainer generation job
- GET /status/:jobId: Check current stage and progress
- GET /download/:jobId: Download rendered MP4 video
- GET /artifact/:jobId/:path: Fetch narration script, qc report, or manifest
- POST /cancel/:jobId: Cancel in-flight generation
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_ENDPOINT = os.environ.get(
    "EXPLAINER_SERVICE_URL", "https://explainer-test-worker.dl-56e.workers.dev"
).rstrip("/")
USER_AGENT = "Cloud-Claw-Video/1.0 (Cloudflare Container Client)"


def _request(url: str, method: str = "GET", data: dict | None = None) -> dict | bytes:
    headers = {"User-Agent": USER_AGENT}
    body = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            content_type = resp.headers.get("Content-Type", "")
            payload = resp.read()
            if "application/json" in content_type:
                return json.loads(payload.decode("utf-8"))
            return payload
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8", errors="replace")
        sys.exit(f"HTTP Error {err.code} from {url}: {err_body}")
    except Exception as err:
        sys.exit(f"Request failed: {err}")


def cmd_create(args: argparse.Namespace) -> None:
    endpoint = args.endpoint.rstrip("/") + "/create"
    payload = {
        "topic": args.topic,
        "durationSeconds": args.duration,
        "purpose": args.purpose,
        "language": args.language,
        "approvalMode": "automatic",
    }
    if args.audience:
        payload["audience"] = args.audience
    if args.script_file:
        script_path = Path(args.script_file)
        if not script_path.exists():
            sys.exit(f"Script file not found: {script_path}")
        payload["scriptText"] = script_path.read_text(encoding="utf-8")
    elif args.script_text:
        payload["scriptText"] = args.script_text
    if args.scene_direction:
        payload["sceneDirection"] = args.scene_direction

    res = _request(endpoint, method="POST", data=payload)
    print(json.dumps(res, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    endpoint = f"{args.endpoint.rstrip('/')}/status/{args.job_id}"
    res = _request(endpoint, method="GET")
    print(json.dumps(res, indent=2))


def cmd_wait(args: argparse.Namespace) -> None:
    job_id = args.job_id
    poll_interval = args.interval
    timeout_sec = args.timeout
    start_time = time.time()

    print(f"Waiting for job {job_id} on {args.endpoint} (interval={poll_interval}s, max_timeout={timeout_sec}s)...")
    while True:
        endpoint = f"{args.endpoint.rstrip('/')}/status/{job_id}"
        data = _request(endpoint, method="GET")
        if not isinstance(data, dict):
            sys.exit("Invalid response from status endpoint")

        status = data.get("status", "unknown")
        progress = data.get("progress", 0)
        stage = data.get("currentStage", "queued")
        print(f"[{time.strftime('%X')}] Status: {status.upper()} ({progress}%) - Stage: {stage}")

        if status == "completed":
            print(f"Job {job_id} successfully completed!")
            if args.download_to:
                dl_path = Path(args.download_to)
                dl_url = f"{args.endpoint.rstrip('/')}/download/{job_id}"
                print(f"Downloading video to {dl_path}...")
                dl_bytes = _request(dl_url, method="GET")
                dl_path.parent.mkdir(parents=True, exist_ok=True)
                dl_path.write_bytes(dl_bytes)
                print(f"Saved {len(dl_bytes)} bytes to {dl_path}")
            break
        elif status in ("failed", "cancelled"):
            err_msg = data.get("error", "Job ended unexpectedly")
            sys.exit(f"Job {job_id} {status}: {err_msg}")

        if time.time() - start_time > timeout_sec:
            sys.exit(f"Timed out after {timeout_sec}s waiting for job {job_id}")

        time.sleep(poll_interval)


def cmd_download(args: argparse.Namespace) -> None:
    out_path = Path(args.output or f"{args.job_id}.mp4")
    endpoint = f"{args.endpoint.rstrip('/')}/download/{args.job_id}"
    raw = _request(endpoint, method="GET")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    print(f"Downloaded {len(raw)} bytes to {out_path.resolve()}")


def cmd_cancel(args: argparse.Namespace) -> None:
    endpoint = f"{args.endpoint.rstrip('/')}/cancel/{args.job_id}"
    res = _request(endpoint, method="POST")
    print(json.dumps(res, indent=2))


def cmd_artifact(args: argparse.Namespace) -> None:
    endpoint = f"{args.endpoint.rstrip('/')}/artifact/{args.job_id}/{args.path.lstrip('/')}"
    raw = _request(endpoint, method="GET")
    if isinstance(raw, dict):
        print(json.dumps(raw, indent=2))
    elif isinstance(raw, bytes):
        try:
            print(raw.decode("utf-8"))
        except UnicodeDecodeError:
            out = Path(f"{args.job_id}_{Path(args.path).name}")
            out.write_bytes(raw)
            print(f"Binary artifact saved to {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Anything2Explainer Studio Client")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Explainer worker service URL")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = subparsers.add_parser("create", help="Create a new explainer video")
    p_create.add_argument("--topic", required=True, help="Topic or core subject")
    p_create.add_argument("--duration", type=int, default=30, help="Target duration in seconds (30, 60, 120, 300, 480)")
    p_create.add_argument("--purpose", default="education", choices=["education", "sales", "onboarding", "marketing"])
    p_create.add_argument("--language", default="en", choices=["en", "zh"])
    p_create.add_argument("--audience", default="", help="Target audience description")
    p_create.add_argument("--script-text", default="", help="Custom narration script text")
    p_create.add_argument("--script-file", default="", help="Path to narration script file")
    p_create.add_argument("--scene-direction", default="", help="Visual style or scene direction")
    p_create.set_defaults(func=cmd_create)

    # status
    p_status = subparsers.add_parser("status", help="Get status of an existing job")
    p_status.add_argument("job_id", help="Explainer Job ID (e.g. exp_...)")
    p_status.set_defaults(func=cmd_status)

    # wait
    p_wait = subparsers.add_parser("wait", help="Poll until job completes or fails")
    p_wait.add_argument("job_id", help="Explainer Job ID")
    p_wait.add_argument("--interval", type=int, default=10, help="Polling interval in seconds")
    p_wait.add_argument("--timeout", type=int, default=1800, help="Timeout in seconds")
    p_wait.add_argument("--download-to", default="", help="Path to save MP4 when complete")
    p_wait.set_defaults(func=cmd_wait)

    # download
    p_download = subparsers.add_parser("download", help="Download rendered MP4")
    p_download.add_argument("job_id", help="Explainer Job ID")
    p_download.add_argument("--output", "-o", default=None, help="Output destination path")
    p_download.set_defaults(func=cmd_download)

    # cancel
    p_cancel = subparsers.add_parser("cancel", help="Cancel in-flight job")
    p_cancel.add_argument("job_id", help="Explainer Job ID")
    p_cancel.set_defaults(func=cmd_cancel)

    # artifact
    p_art = subparsers.add_parser("artifact", help="Fetch job artifact (script, qc, manifest)")
    p_art.add_argument("job_id", help="Explainer Job ID")
    p_art.add_argument("path", help="Relative path (e.g. script/narration.md, metadata/qc.json, metadata/manifest.json)")
    p_art.set_defaults(func=cmd_artifact)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
