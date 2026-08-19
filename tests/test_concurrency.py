"""Stress test: real concurrent clients hammering INCR on one key.

This drives the server over real sockets in a background thread rather
than calling store.incr() directly in-process - a direct call would
prove nothing, since the race this is checking for (two clients'
increments overlapping and one getting lost) can only show up on the
actual connection-handling path, not a bare Python function call.
"""

import asyncio
import socket
import threading
import time

from server import serve
from store import new_store

PORT = 16399
NUM_WORKERS = 20
INCREMENTS_EACH = 100


def _run_server_in_background(store):
    """Runs the asyncio server forever in a daemon thread with its own event loop.

    Args:
        store: The keyspace the server should use.
    """
    def _target():
        asyncio.run(serve("127.0.0.1", PORT, store, context={}))

    threading.Thread(target=_target, daemon=True).start()


def _send_command(sock, *args):
    """Encodes and sends one RESP command over a connected socket.

    Args:
        sock: A connected TCP socket.
        *args: The command name and its arguments, as strings.
    """
    parts = "".join(f"${len(a)}\r\n{a}\r\n" for a in args)
    sock.sendall(f"*{len(args)}\r\n{parts}".encode())


def _recv_reply(sock_file):
    """Reads exactly one RESP reply, tolerating replies split across TCP packets.

    Only handles the reply shapes this test's commands actually produce
    (RESP integers and bulk strings) - readline() on a buffered socket
    file correctly blocks for more data instead of returning a partial
    line, which is what makes this safe under concurrent load.

    Args:
        sock_file: A binary file object wrapping a connected socket
            (`sock.makefile("rb")`).

    Returns:
        The raw reply bytes, including its trailing CRLF(s).
    """
    first_line = sock_file.readline()
    if first_line.startswith(b"$") and not first_line.startswith(b"$-1"):
        return first_line + sock_file.readline()
    return first_line


def _incr_worker(key, times, barrier):
    """Opens one connection and sends INCR `times` times after a start signal.

    Args:
        key: The key to increment.
        times: How many INCR commands to send.
        barrier: A threading.Barrier every worker waits on before sending
            its first command, so increments actually overlap instead of
            running one connection at a time.
    """
    sock = socket.create_connection(("127.0.0.1", PORT))
    sock_file = sock.makefile("rb")
    barrier.wait()
    for _ in range(times):
        _send_command(sock, "INCR", key)
        reply = _recv_reply(sock_file)
        assert reply.startswith(b":"), reply
    sock.close()


def test_concurrent_incr_is_exact():
    """20 clients each send 100 INCRs on the same key; the total must be exact."""
    store = new_store()
    _run_server_in_background(store)
    time.sleep(0.3)  # give the server time to bind before clients connect

    barrier = threading.Barrier(NUM_WORKERS)
    workers = [
        threading.Thread(target=_incr_worker, args=("counter", INCREMENTS_EACH, barrier))
        for _ in range(NUM_WORKERS)
    ]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    sock = socket.create_connection(("127.0.0.1", PORT))
    sock_file = sock.makefile("rb")
    _send_command(sock, "GET", "counter")
    reply = _recv_reply(sock_file)

    expected = NUM_WORKERS * INCREMENTS_EACH
    assert reply == f"${len(str(expected))}\r\n{expected}\r\n".encode()
