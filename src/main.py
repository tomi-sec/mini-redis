"""CLI entrypoint for the Redis-compatible server.

Parses --host/--port/--appendonly-file, replays any existing AOF file
into a fresh store, opens the AOF for further appends, and serves
connections until interrupted (Ctrl+C).
"""

import argparse
import asyncio
from pathlib import Path

from persistence import load
from server import serve
from store import new_store

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def main():
    """Parses CLI arguments, restores from the AOF, and runs the server."""
    parser = argparse.ArgumentParser(description="A Redis-compatible in-memory key-value store")
    parser.add_argument("--host", default="127.0.0.1", help="Host/interface to bind to (default: 127.0.0.1)")
    parser.add_argument(
        "--port",
        type=int,
        # 6380, not Redis's usual 6379, so a real Redis instance can run
        # alongside this one for reference comparisons.
        default=6380,
        help="TCP port to listen on (default: 6380)",
    )
    parser.add_argument(
        "--appendonly-file",
        default=str(DATA_DIR / "appendonly.aof"),
        help="Path to the append-only persistence file",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    aof_path = Path(args.appendonly_file)

    store = new_store()
    replayed = load(aof_path, store)
    print(f"aof: replayed {replayed} command(s) from {aof_path}")

    aof_file = open(aof_path, "ab")
    context = {"aof_file": aof_file}

    try:
        asyncio.run(serve(args.host, args.port, store, context))
    except KeyboardInterrupt:
        pass
    finally:
        aof_file.close()


if __name__ == "__main__":
    main()
