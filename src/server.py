"""The asyncio TCP server: one coroutine per connection, no locks needed.

Real Redis is single-threaded, and that's the whole reason INCR (and
every other command) never races: at any instant only one command is
ever touching the keyspace. asyncio gives the same guarantee here for
free - `handle_connection` never awaits between reading a full command
and finishing that command's store mutation, so the event loop can only
switch to another connection *between* commands, never in the middle of
one. A slow or idle client just means its own coroutine is parked at
`await reader.read(...)`; every other connection's coroutine keeps
running regardless.
"""

import asyncio

import resp
from commands import WRITE_COMMANDS, dispatch
from persistence import append_command
from store import sweep_expired

READ_CHUNK = 4096
ACTIVE_EXPIRE_INTERVAL = 0.1


async def handle_connection(reader, writer, store, context):
    """Serves one client connection until it disconnects or sends garbage.

    Args:
        reader: The connection's asyncio StreamReader.
        writer: The connection's asyncio StreamWriter.
        store: The shared keyspace.
        context: The shared server context dict (see commands.dispatch),
            passed straight through to every command on this connection.
    """
    buffer = b""
    try:
        while True:
            try:
                args, buffer = resp.parse_command(buffer)
            except resp.ProtocolError:
                writer.write(resp.encode_error("ERR Protocol error"))
                await writer.drain()
                break

            if args is None:
                chunk = await reader.read(READ_CHUNK)
                if not chunk:
                    break
                buffer += chunk
                continue

            if not args:
                continue

            reply = dispatch(store, args, context)
            name = args[0].upper()
            if name in WRITE_COMMANDS and not reply.startswith(b"-") and context.get("aof_file"):
                append_command(context["aof_file"], args)

            writer.write(reply)
            await writer.drain()
    except ConnectionResetError:
        pass
    finally:
        writer.close()


async def active_expire_loop(store, interval=ACTIVE_EXPIRE_INTERVAL):
    """Periodically sweeps a random sample of expired keys.

    This is the active half of the TTL strategy - lazy expiration
    (store._get_entry) only catches a key when something happens to read
    it, so a key nobody ever touches again would otherwise stay in
    memory forever after expiring.

    Args:
        store: The shared keyspace.
        interval: Seconds to sleep between sweeps.
    """
    while True:
        await asyncio.sleep(interval)
        sweep_expired(store)


async def serve(host, port, store, context):
    """Starts the TCP server and the active-expiration task, and runs forever.

    Args:
        host: Host/interface to bind to.
        port: TCP port to listen on.
        store: The shared keyspace.
        context: The shared server context dict.
    """

    async def _on_connect(reader, writer):
        await handle_connection(reader, writer, store, context)

    server = await asyncio.start_server(_on_connect, host, port)
    asyncio.create_task(active_expire_loop(store))

    addr = server.sockets[0].getsockname()
    print(f"listening on {addr[0]}:{addr[1]}")
    async with server:
        await server.serve_forever()
