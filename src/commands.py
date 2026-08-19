"""Command dispatch: one function per supported command.

Every handler has the same shape - `(store, args, context) -> bytes` -
where `args` is the parsed command (args[0] is the command name) and
`context` carries server-level resources a handler might need (currently
just the open AOF file, for SAVE). `dispatch` is the single entry point
server.py and persistence.py (for AOF replay) both call.
"""

import os
import time

import resp
from store import (
    CommandError,
    delete,
    exists,
    get_string,
    hdel,
    hget,
    hgetall,
    hset,
    incr,
    llen,
    lpop,
    lpush,
    lrange,
    pttl_ms,
    rpop,
    rpush,
    set_string,
    ttl_seconds,
)

WRITE_COMMANDS = frozenset({
    "SET", "DEL", "INCR", "DECR", "LPUSH", "RPUSH", "LPOP", "RPOP", "HSET", "HDEL",
})


def _check_arity(args, minimum, maximum=None):
    """Raises CommandError if a command got too few or too many arguments.

    Args:
        args: The full parsed command; args[0] is the command name.
        minimum: The minimum allowed length of args (name + required
            arguments).
        maximum: The maximum allowed length of args, or None for no cap.

    Raises:
        CommandError: If len(args) falls outside [minimum, maximum].
    """
    if len(args) < minimum or (maximum is not None and len(args) > maximum):
        raise CommandError(f"ERR wrong number of arguments for '{args[0].lower()}' command")


def cmd_ping(store, args, context):
    """Implements PING [message]."""
    _check_arity(args, 1, 2)
    if len(args) == 2:
        return resp.encode_bulk_string(args[1])
    return resp.encode_simple_string("PONG")


def cmd_echo(store, args, context):
    """Implements ECHO message."""
    _check_arity(args, 2, 2)
    return resp.encode_bulk_string(args[1])


def cmd_command(store, args, context):
    """Implements COMMAND.

    redis-cli sends this on every connect to build its autocomplete list.
    A real implementation returns full per-command metadata; an empty
    array is a valid RESP reply and is enough for redis-cli to proceed
    without it (it just means no autocomplete suggestions).
    """
    return resp.encode_array([])


def cmd_get(store, args, context):
    """Implements GET key."""
    _check_arity(args, 2, 2)
    return resp.encode_bulk_string(get_string(store, args[1]))


def cmd_set(store, args, context):
    """Implements SET key value [EX seconds | PX milliseconds]."""
    _check_arity(args, 3)
    key, value = args[1], args[2]
    expire_at = None

    i = 3
    while i < len(args):
        option = args[i].upper()
        if option in ("EX", "PX") and i + 1 < len(args):
            try:
                amount = int(args[i + 1])
            except ValueError:
                raise CommandError("ERR value is not an integer or out of range") from None
            seconds = amount if option == "EX" else amount / 1000.0
            expire_at = time.time() + seconds
            i += 2
        else:
            raise CommandError("ERR syntax error")

    set_string(store, key, value, expire_at)
    return resp.encode_simple_string("OK")


def cmd_del(store, args, context):
    """Implements DEL key [key ...]."""
    _check_arity(args, 2)
    return resp.encode_integer(delete(store, args[1:]))


def cmd_exists(store, args, context):
    """Implements EXISTS key [key ...]."""
    _check_arity(args, 2)
    return resp.encode_integer(exists(store, args[1:]))


def cmd_ttl(store, args, context):
    """Implements TTL key."""
    _check_arity(args, 2, 2)
    return resp.encode_integer(ttl_seconds(store, args[1]))


def cmd_pttl(store, args, context):
    """Implements PTTL key."""
    _check_arity(args, 2, 2)
    return resp.encode_integer(pttl_ms(store, args[1]))


def cmd_incr(store, args, context):
    """Implements INCR key."""
    _check_arity(args, 2, 2)
    return resp.encode_integer(incr(store, args[1], 1))


def cmd_decr(store, args, context):
    """Implements DECR key."""
    _check_arity(args, 2, 2)
    return resp.encode_integer(incr(store, args[1], -1))


def cmd_lpush(store, args, context):
    """Implements LPUSH key value [value ...]."""
    _check_arity(args, 3)
    return resp.encode_integer(lpush(store, args[1], args[2:]))


def cmd_rpush(store, args, context):
    """Implements RPUSH key value [value ...]."""
    _check_arity(args, 3)
    return resp.encode_integer(rpush(store, args[1], args[2:]))


def cmd_lpop(store, args, context):
    """Implements LPOP key."""
    _check_arity(args, 2, 2)
    return resp.encode_bulk_string(lpop(store, args[1]))


def cmd_rpop(store, args, context):
    """Implements RPOP key."""
    _check_arity(args, 2, 2)
    return resp.encode_bulk_string(rpop(store, args[1]))


def cmd_lrange(store, args, context):
    """Implements LRANGE key start stop."""
    _check_arity(args, 4, 4)
    try:
        start, stop = int(args[2]), int(args[3])
    except ValueError:
        raise CommandError("ERR value is not an integer or out of range") from None
    values = lrange(store, args[1], start, stop)
    return resp.encode_array([resp.encode_bulk_string(v) for v in values])


def cmd_llen(store, args, context):
    """Implements LLEN key."""
    _check_arity(args, 2, 2)
    return resp.encode_integer(llen(store, args[1]))


def cmd_hset(store, args, context):
    """Implements HSET key field value [field value ...]."""
    _check_arity(args, 4)
    pairs = args[2:]
    if len(pairs) % 2 != 0:
        raise CommandError("ERR wrong number of arguments for 'hset' command")
    field_values = dict(zip(pairs[0::2], pairs[1::2]))
    return resp.encode_integer(hset(store, args[1], field_values))


def cmd_hget(store, args, context):
    """Implements HGET key field."""
    _check_arity(args, 3, 3)
    return resp.encode_bulk_string(hget(store, args[1], args[2]))


def cmd_hdel(store, args, context):
    """Implements HDEL key field [field ...]."""
    _check_arity(args, 3)
    return resp.encode_integer(hdel(store, args[1], args[2:]))


def cmd_hgetall(store, args, context):
    """Implements HGETALL key.

    Returns fields and values as one flat array (field, value, field,
    value, ...), matching real Redis's wire format rather than nesting
    pairs.
    """
    _check_arity(args, 2, 2)
    flat = []
    for field, value in hgetall(store, args[1]).items():
        flat.append(resp.encode_bulk_string(field))
        flat.append(resp.encode_bulk_string(value))
    return resp.encode_array(flat)


def cmd_save(store, args, context):
    """Implements SAVE.

    This server's persistence is an append-only log (see persistence.py)
    rather than a point-in-time snapshot, so every write is already
    durable by the time its reply is sent. SAVE's job here is just to
    force the AOF file to disk immediately instead of waiting on the OS's
    normal write-back schedule.
    """
    aof_file = context.get("aof_file")
    if aof_file is not None:
        aof_file.flush()
        os.fsync(aof_file.fileno())
    return resp.encode_simple_string("OK")


COMMANDS = {
    "PING": cmd_ping,
    "ECHO": cmd_echo,
    "COMMAND": cmd_command,
    "GET": cmd_get,
    "SET": cmd_set,
    "DEL": cmd_del,
    "EXISTS": cmd_exists,
    "TTL": cmd_ttl,
    "PTTL": cmd_pttl,
    "INCR": cmd_incr,
    "DECR": cmd_decr,
    "LPUSH": cmd_lpush,
    "RPUSH": cmd_rpush,
    "LPOP": cmd_lpop,
    "RPOP": cmd_rpop,
    "LRANGE": cmd_lrange,
    "LLEN": cmd_llen,
    "HSET": cmd_hset,
    "HGET": cmd_hget,
    "HDEL": cmd_hdel,
    "HGETALL": cmd_hgetall,
    "SAVE": cmd_save,
}


def dispatch(store, args, context=None):
    """Looks up and runs the handler for one parsed command.

    Args:
        store: The keyspace, as returned by store.new_store().
        args: The parsed command, e.g. ["SET", "k", "v"] - args[0] is
            matched against COMMANDS case-insensitively.
        context: Optional dict of server-level resources a handler might
            need (currently just "aof_file"). Defaults to an empty dict
            for callers, like tests and AOF replay, that don't need any.

    Returns:
        The RESP-encoded reply bytes - always a valid reply, never an
        exception: unknown commands and command-level errors (wrong
        type, bad arity, non-integer INCR target) are all turned into a
        RESP error reply here rather than raised further.
    """
    context = context or {}
    name = args[0].upper()
    handler = COMMANDS.get(name)
    if handler is None:
        return resp.encode_error(f"ERR unknown command '{args[0]}'")
    try:
        return handler(store, args, context)
    except CommandError as exc:
        return resp.encode_error(str(exc))
