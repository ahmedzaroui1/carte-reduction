#!/usr/bin/env python3
"""
Sync the cartes_reduction project to a distant server over SSH/rsync.

Usage:
    python3 sync_remote.py <target_ip> [--user <ssh_user>] [--remote-path <path>] [--dry-run] [--restart]

The target IP is required. SSH key-based authentication is assumed.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# Files/dirs that should never be pushed to the remote server
EXCLUDES = [
    "__pycache__/",
    "*.py[cod]",
    ".git/",
    ".gitignore",
    ".env",
    "docker-compose*.yml",
    "Dockerfile",
    "odoo.conf",
    ".dockerignore",
    "*.tar.gz",
    "venv/",
    ".vscode/",
    ".idea/",
]


def build_rsync_args(target_ip: str, user: str, remote_path: str, dry_run: bool) -> list[str]:
    host = f"{user}@{target_ip}"
    args = ["rsync", "-avz", "--delete", "--progress"]
    for pattern in EXCLUDES:
        args.append(f"--exclude={pattern}")
    if dry_run:
        args.append("--dry-run")
    args.append(str(PROJECT_ROOT) + "/")
    args.append(f"{host}:{remote_path}/")
    return args


def remote_cmd(target_ip: str, user: str, command: str) -> None:
    host = f"{user}@{target_ip}"
    ssh_cmd = ["ssh", host, command]
    print(f"$ ssh {host} {command}")
    subprocess.run(ssh_cmd, check=True)


def restart_remote(target_ip: str, user: str, remote_path: str) -> None:
    remote_cmd(
        target_ip,
        user,
        f"cd {shlex.quote(remote_path)} && docker-compose down && docker-compose up -d --build",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync cartes_reduction to a distant server.")
    parser.add_argument("target_ip", help="Target server IP address")
    parser.add_argument("--user", default=os.getenv("USER", "root"), help="SSH user (default: current user)")
    parser.add_argument(
        "--remote-path",
        default=os.getenv("REMOTE_PATH", "/opt/cartes_reduction"),
        help="Remote destination path (default: /opt/cartes_reduction)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be transferred without doing it")
    parser.add_argument("--restart", action="store_true", help="Restart docker-compose on the remote after sync")
    args = parser.parse_args()

    if not shutil.which("rsync"):
        print("ERROR: rsync is not installed. Install it: sudo apt install rsync", file=sys.stderr)
        return 1
    if not shutil.which("ssh"):
        print("ERROR: ssh is not installed. Install it: sudo apt install openssh-client", file=sys.stderr)
        return 1

    print(f"Syncing {PROJECT_ROOT} -> {args.user}@{args.target_ip}:{args.remote_path}")
    if args.dry_run:
        print("DRY-RUN mode: no files will be transferred")

    args_list = build_rsync_args(args.target_ip, args.user, args.remote_path, args.dry_run)
    print(f"$ {' '.join(shlex.quote(a) for a in args_list)}")
    try:
        subprocess.run(args_list, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"rsync failed with exit code {exc.returncode}", file=sys.stderr)
        return exc.returncode

    print("Sync complete.")

    if args.restart and not args.dry_run:
        print("Restarting services on remote...")
        try:
            restart_remote(args.target_ip, args.user, args.remote_path)
            print("Remote services restarted.")
        except subprocess.CalledProcessError as exc:
            print(f"Remote restart failed with exit code {exc.returncode}", file=sys.stderr)
            return exc.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
