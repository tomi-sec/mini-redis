"""Append-only persistence: every write command, replayed on startup.

Chosen over RDB-style point-in-time snapshots because it composes
directly with the RESP parser already written for the wire protocol - an
AOF entry here is exactly a RESP-encoded command, so `load` reuses
resp.parse_command instead of inventing a second file format. The
tradeoff (see the README) is a bigger file on disk and a slower cold
start than a binary snapshot, since startup replays every write ever
made instead of loading one point-in-time image.
"""

import sys
from pathlib import Path

import resp
from commands import dispatch


def append_command(aof_file, args):
    """Appends one command to the AOF file and flushes it to the OS.

    Flushing (but not calling fsync) on every write means a crash loses
    at most whatever the OS was still holding in its own write buffer -
    a full fsync-per-write would be more durable and much slower; SAVE
    (see commands.cmd_save) is the escape hatch for forcing an fsync.

    Args:
        aof_file: The open (append-mode, binary) AOF file handle.
        args: The command as parsed, e.g. ["SET", "k", "v"].
    """
    entry = resp.encode_array([resp.encode_bulk_string(arg) for arg in args])
    aof_file.write(entry)
    aof_file.flush()


def load(path, store):
    """Replays an AOF file into a store, tolerating a half-written tail.

    A crash mid-write can leave the last entry in the file truncated -
    resp.parse_command reports that as "not a complete command yet"
    rather than an error, so it's treated the same way here: replay
    stops there and the incomplete trailing bytes are discarded (with a
    warning), instead of failing startup over a write that never
    finished anyway.

    Args:
        path: Path to the AOF file. A missing file is treated as an
            empty, valid log.
        store: The keyspace to replay commands into.

    Returns:
        The number of commands successfully replayed.
    """
    try:
        buffer = Path(path).read_bytes()
    except FileNotFoundError:
        return 0

    replayed = 0
    while buffer:
        try:
            args, buffer = resp.parse_command(buffer)
        except resp.ProtocolError as exc:
            print(f"aof: stopping replay at a malformed entry ({exc})", file=sys.stderr)
            break
        if args is None:
            print(
                f"aof: discarding {len(buffer)} trailing bytes of an incomplete entry",
                file=sys.stderr,
            )
            break
        if args:
            dispatch(store, args)
            replayed += 1
    return replayed
