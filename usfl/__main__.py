"""Command line: python3 -m usfl <fetch|build|verify|export> [options]."""
from __future__ import annotations

import sys

USAGE = """usage: python3 -m usfl <command> [options]

commands:
  fetch    download the official sources and verify them against manifests/
  build    map the sources into SQLite (schema/schema.sql)
  verify   integrity and count checks on a built database
  export   per-corpus databases, JSONL/CSV exports, compression and SHA256SUMS

Run `python3 -m usfl <command> --help` for each command's options.
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(USAGE)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "fetch":
        from .fetch import main as run
    elif cmd == "build":
        from .build import main as run
    elif cmd == "verify":
        from .verify import main as run
    elif cmd == "export":
        from .export import main as run
    else:
        print(USAGE, file=sys.stderr)
        return 2
    return run(rest)


if __name__ == "__main__":
    sys.exit(main())
