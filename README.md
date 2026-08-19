# Redis-Compatible Key-Value Store

A Redis server built from scratch on top of raw sockets - RESP protocol parsing, TTL
expiry, lists, hashes, and append-only persistence - to actually answer what my
capstone's queue broker was doing under the hood instead of just calling into it.
Verified against the real `redis-cli`.

## How it works

`resp.py` is the only file that knows the wire format: it turns raw socket bytes into
`["SET", "k", "v"]`-style argument lists (handling a command split across multiple
reads) and serializes replies back into RESP's five reply types. `store.py` holds the
actual keyspace - a plain dict, string/list/hash values, TTLs - and `commands.py`
dispatches each parsed command to a handler. Keys expire two ways at once, like real
Redis: lazily, when something reads a stale key, and actively, via a background sweep
that samples keys with a TTL every 100ms so an untouched key doesn't just sit expired
in memory forever.

## Concurrency and persistence

The server runs on a single asyncio event loop instead of a thread per connection -
the same reason real Redis is single-threaded: a command handler runs start-to-finish
before the loop can switch to another connection, so `store.py` needs zero locks and
INCR can never lose an update to a race. I proved this instead of assuming it: 20 real
socket clients each fire 100 concurrent INCRs at the same key, and the final value
comes out exactly 2000, every time.

Persistence is an append-only log rather than a point-in-time snapshot, and it reuses
the RESP parser: every write command gets logged in its own wire format, so replaying
it on startup is just running the same parser over a file instead of a socket. That
also gave me the "half-written file" case for free - a crash mid-write leaves a
truncated final entry that `parse_command` reports as incomplete rather than
malformed, so it's discarded on load instead of corrupting the whole log.

## Verified against real Redis

I ran the actual `redis-cli` (via Docker, against my server on the host) through PING,
GET/SET/DEL/EXISTS, EX/PX expiry, INCR/DECR, LPUSH/RPUSH/LRANGE/LPOP, and
HSET/HGET/HGETALL, plus the WRONGTYPE error path, a key actually expiring mid-TTL, and
a full process restart with the data still intact afterward. Every reply matched real
Redis's behavior and error text, including LRANGE's negative indexing.

## Running it

```
pip install -r requirements.txt
pytest                                 # 62 tests: protocol, storage, commands, persistence, concurrency

python src/main.py --port 6380         # start the server

# from another terminal, or a dockerized redis-cli:
redis-cli -p 6380 ping
```
