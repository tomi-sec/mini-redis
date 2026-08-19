"""RESP2 wire protocol: parsing client requests and serializing replies.

Real clients (redis-cli included) always send a command as a RESP array of
bulk strings, e.g. b"*1\\r\\n$4\\r\\nPING\\r\\n" for PING with no arguments.
Replies can be any of the five RESP2 reply types (simple string, error,
integer, bulk string, array). This module is deliberately the only place
that knows about "*", "$", "+", "-", ":" - everything else in the server
works with plain Python strings, ints, and lists.
"""

CRLF = b"\r\n"


class ProtocolError(Exception):
    """Raised when a client's bytes don't parse as a RESP request."""


def _read_line(buffer, start):
    """Locates a CRLF-terminated line in a buffer starting at an offset.

    Args:
        buffer: The full receive buffer.
        start: Offset to start scanning from.

    Returns:
        A tuple `(line, next_offset)` with the line's bytes (CRLF
        excluded) if a terminator was found at or after `start`, else
        `(None, start)` meaning the line isn't complete yet.
    """
    end = buffer.find(CRLF, start)
    if end == -1:
        return None, start
    return buffer[start:end], end + 2


def parse_command(buffer):
    """Attempts to parse one full RESP array-of-bulk-strings command.

    A command can arrive split across multiple socket reads, so this only
    consumes bytes when a complete command is present in `buffer` -
    otherwise it reports that more bytes are needed and leaves the buffer
    untouched, so the caller can read more and retry with the combined
    buffer.

    Args:
        buffer: The bytes accumulated from the socket so far.

    Returns:
        A tuple `(args, rest)`. If a full command was present, `args` is
        the list of decoded argument strings (e.g. ["SET", "k", "v"]) and
        `rest` is `buffer` with that command's bytes removed. If the
        buffer doesn't yet hold a complete command, `args` is None and
        `rest` is the original, unmodified `buffer`.

    Raises:
        ProtocolError: If the buffer's contents don't match the RESP
            array-of-bulk-strings request format (e.g. wrong leading
            byte, a non-integer length).
    """
    if not buffer:
        return None, buffer

    if buffer[0:1] != b"*":
        raise ProtocolError(f"expected '*', got {buffer[0:1]!r}")

    header, pos = _read_line(buffer, 1)
    if header is None:
        return None, buffer
    try:
        count = int(header)
    except ValueError:
        raise ProtocolError(f"invalid array length {header!r}") from None
    if count <= 0:
        return [], buffer[pos:]

    args = []
    for _ in range(count):
        if pos >= len(buffer):
            return None, buffer
        if buffer[pos:pos + 1] != b"$":
            raise ProtocolError(f"expected '$', got {buffer[pos:pos + 1]!r}")

        len_line, next_pos = _read_line(buffer, pos + 1)
        if len_line is None:
            return None, buffer
        try:
            length = int(len_line)
        except ValueError:
            raise ProtocolError(f"invalid bulk string length {len_line!r}") from None

        data_start = next_pos
        data_end = data_start + length
        if data_end + 2 > len(buffer):
            return None, buffer

        args.append(buffer[data_start:data_end].decode("utf-8", errors="replace"))
        pos = data_end + 2

    return args, buffer[pos:]


def encode_simple_string(text):
    """Encodes a RESP simple string reply (e.g. "+OK").

    Args:
        text: The string to encode. Real Redis simple strings never
            contain CR/LF; callers only use this for fixed, known-safe
            replies like "OK" and "PONG".

    Returns:
        The wire-format bytes for the reply.
    """
    return b"+" + text.encode("utf-8") + CRLF


def encode_error(message):
    """Encodes a RESP error reply (e.g. "-ERR unknown command").

    Args:
        message: The error text, conventionally starting with an
            all-caps error kind ("ERR", "WRONGTYPE") like real Redis.

    Returns:
        The wire-format bytes for the reply.
    """
    return b"-" + message.encode("utf-8") + CRLF


def encode_integer(number):
    """Encodes a RESP integer reply (e.g. ":1000").

    Args:
        number: The integer to encode.

    Returns:
        The wire-format bytes for the reply.
    """
    return b":" + str(number).encode("ascii") + CRLF


def encode_bulk_string(value):
    """Encodes a RESP bulk string reply, or the null bulk string.

    Args:
        value: The string (or raw bytes) to encode, or None for the RESP
            null bulk string ("$-1"), which is what real Redis returns
            for GET on a missing key.

    Returns:
        The wire-format bytes for the reply.
    """
    if value is None:
        return b"$-1" + CRLF
    data = value.encode("utf-8") if isinstance(value, str) else value
    return b"$" + str(len(data)).encode("ascii") + CRLF + data + CRLF


def encode_array(items):
    """Encodes a RESP array reply from already-encoded reply items.

    Args:
        items: A list of already-encoded reply bytes (e.g. produced by
            `encode_bulk_string`), or None for the RESP null array
            ("*-1").

    Returns:
        The wire-format bytes for the reply.
    """
    if items is None:
        return b"*-1" + CRLF
    return b"*" + str(len(items)).encode("ascii") + CRLF + b"".join(items)
