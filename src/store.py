"""The in-memory keyspace: string/list/hash storage plus TTL expiry.

A store is a plain dict with two parts - `data` (key -> entry) and
`expiring` (the set of keys that currently have a TTL, kept in sync by
every function below) so the active expiration sweep in main.py can
sample only keys that can actually expire instead of scanning everything.
Every entry is `{"value": ..., "type": "string" | "list" | "hash",
"expire_at": float epoch seconds or None}`.
"""

import random
import time

STRING, LIST, HASH = "string", "list", "hash"


class CommandError(Exception):
    """An error a command handler wants sent back to the client verbatim.

    The message is used as-is in a RESP error reply, so it follows real
    Redis's convention of leading with an all-caps error kind, e.g.
    "WRONGTYPE Operation against a key holding the wrong kind of value"
    or "ERR value is not an integer or out of range".
    """


def new_store():
    """Creates an empty keyspace.

    Returns:
        A dict with a "data" dict and an "expiring" set, ready to pass to
        every other function in this module.
    """
    return {"data": {}, "expiring": set()}


def _get_entry(store, key, expected_type=None):
    """Looks up a key's entry, applying lazy TTL expiry and a type check.

    This is the choke point almost every command goes through: a key
    whose expire_at has passed is deleted here and treated as absent
    (lazy expiration - the active sweep in main.py is the other half of
    the strategy, for keys nobody happens to read), and a type mismatch
    raises the same WRONGTYPE message real Redis uses.

    Args:
        store: The keyspace, as returned by new_store().
        key: The key to look up.
        expected_type: If given, the entry's "type" must match this or a
            CommandError is raised.

    Returns:
        The entry dict, or None if the key doesn't exist (or just
        expired).

    Raises:
        CommandError: If expected_type is given and doesn't match the
            entry's actual type.
    """
    entry = store["data"].get(key)
    if entry is None:
        return None
    if entry["expire_at"] is not None and entry["expire_at"] <= time.time():
        del store["data"][key]
        store["expiring"].discard(key)
        return None
    if expected_type is not None and entry["type"] != expected_type:
        raise CommandError("WRONGTYPE Operation against a key holding the wrong kind of value")
    return entry


def _set_entry(store, key, value, entry_type, expire_at=None):
    """Writes a key's entry, replacing whatever was there before.

    Args:
        store: The keyspace.
        key: The key to write.
        value: The new value (a str for STRING, a list for LIST, a dict
            for HASH).
        entry_type: One of STRING, LIST, HASH.
        expire_at: Absolute expiry time in epoch seconds, or None for no
            expiry.
    """
    store["data"][key] = {"value": value, "type": entry_type, "expire_at": expire_at}
    if expire_at is None:
        store["expiring"].discard(key)
    else:
        store["expiring"].add(key)


def sweep_expired(store, sample_size=20):
    """Actively evicts a random sample of expired keys.

    Real Redis does this alongside lazy expiration because a key that's
    never read again (e.g. a session token nobody checks after it goes
    stale) would otherwise sit in memory forever under lazy-only expiry.
    Sampling instead of a full scan keeps this cheap enough to call on a
    timer without pausing the event loop.

    Args:
        store: The keyspace.
        sample_size: How many keys with a TTL to sample this call.

    Returns:
        The number of keys evicted.
    """
    candidates = random.sample(
        sorted(store["expiring"]), k=min(sample_size, len(store["expiring"]))
    )
    evicted = 0
    now = time.time()
    for key in candidates:
        entry = store["data"].get(key)
        if entry is not None and entry["expire_at"] is not None and entry["expire_at"] <= now:
            del store["data"][key]
            store["expiring"].discard(key)
            evicted += 1
    return evicted


def set_string(store, key, value, expire_at=None):
    """Sets a string key, overwriting any existing value/type at that key.

    Args:
        store: The keyspace.
        key: The key to set.
        value: The string value.
        expire_at: Absolute expiry time in epoch seconds, or None.
    """
    _set_entry(store, key, value, STRING, expire_at)


def get_string(store, key):
    """Reads a string key.

    Args:
        store: The keyspace.
        key: The key to read.

    Returns:
        The string value, or None if the key doesn't exist or is
        expired.

    Raises:
        CommandError: If the key holds a non-string value.
    """
    entry = _get_entry(store, key, expected_type=STRING)
    return entry["value"] if entry else None


def delete(store, keys):
    """Deletes zero or more keys, regardless of their type.

    Args:
        store: The keyspace.
        keys: An iterable of keys to delete.

    Returns:
        The number of keys that actually existed and were removed
        (already-expired keys don't count, matching real Redis).
    """
    removed = 0
    for key in keys:
        if _get_entry(store, key) is not None:
            del store["data"][key]
            store["expiring"].discard(key)
            removed += 1
    return removed


def exists(store, keys):
    """Counts how many of the given keys currently exist.

    Args:
        store: The keyspace.
        keys: An iterable of keys to check. A key repeated in the input
            is counted once per occurrence, matching real Redis's EXISTS.

    Returns:
        The number of times a given key was found to exist.
    """
    return sum(1 for key in keys if _get_entry(store, key) is not None)


def ttl_seconds(store, key):
    """Reads a key's remaining time-to-live, in whole seconds.

    Args:
        store: The keyspace.
        key: The key to check.

    Returns:
        The remaining TTL rounded to the nearest second, -1 if the key
        exists but has no expiry, or -2 if the key doesn't exist.
    """
    return _ttl(store, key, divisor=1.0)


def pttl_ms(store, key):
    """Reads a key's remaining time-to-live, in milliseconds.

    Args:
        store: The keyspace.
        key: The key to check.

    Returns:
        The remaining TTL in whole milliseconds, -1 if the key exists but
        has no expiry, or -2 if the key doesn't exist.
    """
    return _ttl(store, key, divisor=0.001)


def _ttl(store, key, divisor):
    """Shared implementation for ttl_seconds and pttl_ms.

    Args:
        store: The keyspace.
        key: The key to check.
        divisor: 1.0 for whole-second units, 0.001 for millisecond units.

    Returns:
        The remaining TTL in the requested unit, or the -1/-2 sentinels
        described in ttl_seconds/pttl_ms.
    """
    entry = _get_entry(store, key)
    if entry is None:
        return -2
    if entry["expire_at"] is None:
        return -1
    return max(0, round((entry["expire_at"] - time.time()) / divisor))


def incr(store, key, delta):
    """Adds delta to an integer-valued string key, creating it if absent.

    Args:
        store: The keyspace.
        key: The key to increment.
        delta: The signed amount to add (INCR passes 1, DECR passes -1).

    Returns:
        The new integer value.

    Raises:
        CommandError: If the key holds a non-string value, or a string
            that isn't a base-10 integer.
    """
    entry = _get_entry(store, key, expected_type=STRING)
    current = entry["value"] if entry else "0"
    try:
        new_value = int(current) + delta
    except ValueError:
        raise CommandError("ERR value is not an integer or out of range") from None
    expire_at = entry["expire_at"] if entry else None
    _set_entry(store, key, str(new_value), STRING, expire_at)
    return new_value


def _list_entry(store, key):
    """Fetches (or lazily creates) a key's list, for the LPUSH/RPUSH family.

    Args:
        store: The keyspace.
        key: The key to fetch.

    Returns:
        The entry dict for this key, creating an empty LIST entry first
        if the key didn't already exist.

    Raises:
        CommandError: If the key holds a non-list value.
    """
    entry = _get_entry(store, key, expected_type=LIST)
    if entry is None:
        _set_entry(store, key, [], LIST)
        entry = store["data"][key]
    return entry


def lpush(store, key, values):
    """Pushes values onto the left (head) of a list, one at a time.

    Args:
        store: The keyspace.
        key: The key to push onto; created as an empty list if absent.
        values: Values to push, each in turn inserted at the new head -
            so the last value in `values` ends up first in the list,
            matching real Redis's LPUSH semantics.

    Returns:
        The list's length after all pushes.

    Raises:
        CommandError: If the key holds a non-list value.
    """
    entry = _list_entry(store, key)
    for value in values:
        entry["value"].insert(0, value)
    return len(entry["value"])


def rpush(store, key, values):
    """Pushes values onto the right (tail) of a list, one at a time.

    Args:
        store: The keyspace.
        key: The key to push onto; created as an empty list if absent.
        values: Values to push, each appended in order.

    Returns:
        The list's length after all pushes.

    Raises:
        CommandError: If the key holds a non-list value.
    """
    entry = _list_entry(store, key)
    entry["value"].extend(values)
    return len(entry["value"])


def lpop(store, key):
    """Pops and returns the leftmost (head) element of a list.

    Args:
        store: The keyspace.
        key: The key to pop from.

    Returns:
        The popped value, or None if the key doesn't exist or the list is
        already empty. A list that becomes empty from this pop is removed
        from the keyspace entirely, matching real Redis.

    Raises:
        CommandError: If the key holds a non-list value.
    """
    return _list_pop(store, key, index=0)


def rpop(store, key):
    """Pops and returns the rightmost (tail) element of a list.

    Args:
        store: The keyspace.
        key: The key to pop from.

    Returns:
        The popped value, or None if the key doesn't exist or the list is
        already empty. A list that becomes empty from this pop is removed
        from the keyspace entirely, matching real Redis.

    Raises:
        CommandError: If the key holds a non-list value.
    """
    return _list_pop(store, key, index=-1)


def _list_pop(store, key, index):
    """Shared implementation for lpop/rpop.

    Args:
        store: The keyspace.
        key: The key to pop from.
        index: 0 for the head (LPOP), -1 for the tail (RPOP).

    Returns:
        The popped value, or None per lpop/rpop's documented behavior.
    """
    entry = _get_entry(store, key, expected_type=LIST)
    if entry is None or not entry["value"]:
        return None
    value = entry["value"].pop(index)
    if not entry["value"]:
        delete(store, [key])
    return value


def lrange(store, key, start, stop):
    """Reads an inclusive range of a list's elements, Python-slice style.

    Args:
        store: The keyspace.
        key: The key to read.
        start: Start index; negative counts from the end (-1 is last).
        stop: Stop index, inclusive; negative counts from the end.

    Returns:
        A list of elements in [start, stop], or an empty list if the key
        doesn't exist. Out-of-range indexes are clamped rather than
        erroring, matching real Redis's LRANGE.

    Raises:
        CommandError: If the key holds a non-list value.
    """
    entry = _get_entry(store, key, expected_type=LIST)
    if entry is None:
        return []
    values = entry["value"]
    length = len(values)

    if stop < 0:
        stop = max(length + stop, -1)
    stop = min(stop, length - 1)
    if start < 0:
        start = max(length + start, 0)

    if start > stop:
        return []
    return values[start:stop + 1]


def llen(store, key):
    """Reads a list's length.

    Args:
        store: The keyspace.
        key: The key to check.

    Returns:
        The list's length, or 0 if the key doesn't exist.

    Raises:
        CommandError: If the key holds a non-list value.
    """
    entry = _get_entry(store, key, expected_type=LIST)
    return len(entry["value"]) if entry else 0


def hset(store, key, field_values):
    """Sets one or more fields in a hash, creating the hash if absent.

    Args:
        store: The keyspace.
        key: The key to write to; created as an empty hash if absent.
        field_values: A dict of field -> value pairs to set.

    Returns:
        The number of fields that were newly created (not counting
        fields that already existed and just got a new value),
        matching real Redis's HSET return value.

    Raises:
        CommandError: If the key holds a non-hash value.
    """
    entry = _get_entry(store, key, expected_type=HASH)
    if entry is None:
        _set_entry(store, key, {}, HASH)
        entry = store["data"][key]
    created = sum(1 for field in field_values if field not in entry["value"])
    entry["value"].update(field_values)
    return created


def hget(store, key, field):
    """Reads one field of a hash.

    Args:
        store: The keyspace.
        key: The key to read.
        field: The hash field to read.

    Returns:
        The field's value, or None if the key or the field doesn't
        exist.

    Raises:
        CommandError: If the key holds a non-hash value.
    """
    entry = _get_entry(store, key, expected_type=HASH)
    return entry["value"].get(field) if entry else None


def hdel(store, key, fields):
    """Deletes one or more fields from a hash.

    Args:
        store: The keyspace.
        key: The key to modify.
        fields: An iterable of field names to remove.

    Returns:
        The number of fields that actually existed and were removed. A
        hash that becomes empty from this delete is removed from the
        keyspace entirely, matching real Redis.

    Raises:
        CommandError: If the key holds a non-hash value.
    """
    entry = _get_entry(store, key, expected_type=HASH)
    if entry is None:
        return 0
    removed = 0
    for field in fields:
        if entry["value"].pop(field, None) is not None:
            removed += 1
    if not entry["value"]:
        delete(store, [key])
    return removed


def hgetall(store, key):
    """Reads every field/value pair in a hash.

    Args:
        store: The keyspace.
        key: The key to read.

    Returns:
        A dict copy of the hash's fields, or an empty dict if the key
        doesn't exist.

    Raises:
        CommandError: If the key holds a non-hash value.
    """
    entry = _get_entry(store, key, expected_type=HASH)
    return dict(entry["value"]) if entry else {}
