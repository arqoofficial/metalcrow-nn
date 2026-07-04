#!/usr/bin/env python3
"""Generate judge_* users and print credentials to stdout.

Usage:
  # локально (из backend/)
  uv run python scripts/generate_judge_users.py --count 5

  # в Docker (backend-контейнер, venv уже в PATH)
  python scripts/generate_judge_users.py --count 5
"""

from __future__ import annotations

import argparse
import secrets
import sys

from sqlmodel import Session

from app import crud
from app.core.db import engine
from app.models import UserCreate


def judge_name(user_id: int) -> str:
    return f"judge_{user_id}"


def judge_email(user_id: int) -> str:
    return f"judge_{user_id}@metalcrow.com"


def generate_password() -> str:
    return secrets.token_urlsafe(16)


def parse_ids(args: argparse.Namespace) -> list[int]:
    if args.ids:
        return sorted({int(x.strip()) for x in args.ids.split(",") if x.strip()})
    if args.start is not None or args.end is not None:
        if args.start is None or args.end is None:
            raise SystemExit("--start and --end must be used together")
        if args.start > args.end:
            raise SystemExit("--start must be <= --end")
        return list(range(args.start, args.end + 1))
    return list(range(1, args.count + 1))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate judge users")
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="How many users to create: judge_1 .. judge_N (default: 1)",
    )
    parser.add_argument("--start", type=int, help="First numeric id (inclusive)")
    parser.add_argument("--end", type=int, help="Last numeric id (inclusive)")
    parser.add_argument("--ids", help="Comma-separated ids, e.g. 1,3,7")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print credentials, do not write to the database",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip users that already exist in the database",
    )
    args = parser.parse_args(argv)

    try:
        ids = parse_ids(args)
    except ValueError as exc:
        print(f"Invalid id list: {exc}", file=sys.stderr)
        return 1

    if not ids:
        print("No user ids to generate", file=sys.stderr)
        return 1

    rows: list[tuple[str, str]] = []

    with Session(engine) as session:
        for user_id in ids:
            name = judge_name(user_id)
            email = judge_email(user_id)
            password = generate_password()

            if not args.dry_run:
                existing = crud.get_user_by_email(session=session, email=email)
                if existing:
                    if args.skip_existing:
                        print(f"skip existing: {email}", file=sys.stderr)
                        continue
                    print(f"User already exists: {email}", file=sys.stderr)
                    return 1

                user_in = UserCreate(
                    email=email,
                    full_name=name,
                    password=password,
                )
                crud.create_user(session=session, user_create=user_in)

            rows.append((email, password))

    if not rows:
        print("No users generated", file=sys.stderr)
        return 1

    for email, password in rows:
        print(f"{email}\t{password}")

    if args.dry_run:
        print(f"\n(dry-run: {len(rows)} user(s), not saved to database)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
