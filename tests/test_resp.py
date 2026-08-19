import pytest

import resp


def test_parse_command_reads_a_single_full_command():
    """A complete RESP array-of-bulk-strings command parses in one call."""
    buffer = b"*2\r\n$3\r\nGET\r\n$1\r\nk\r\n"
    args, rest = resp.parse_command(buffer)
    assert args == ["GET", "k"]
    assert rest == b""


def test_parse_command_leaves_extra_bytes_for_the_next_call():
    """A buffer holding two commands only consumes the first one."""
    buffer = b"*1\r\n$4\r\nPING\r\n*1\r\n$4\r\nPING\r\n"
    args, rest = resp.parse_command(buffer)
    assert args == ["PING"]
    args2, rest2 = resp.parse_command(rest)
    assert args2 == ["PING"]
    assert rest2 == b""


def test_parse_command_split_across_multiple_reads():
    """A command arriving in several socket chunks only parses once complete."""
    whole = b"*3\r\n$3\r\nSET\r\n$1\r\nk\r\n$5\r\nhello\r\n"
    for cut in range(1, len(whole)):
        first, second = whole[:cut], whole[cut:]
        args, rest = resp.parse_command(first)
        assert args is None
        assert rest == first
        args, rest = resp.parse_command(first + second)
        assert args == ["SET", "k", "hello"]
        assert rest == b""


def test_parse_command_on_empty_buffer_reports_incomplete():
    """An empty buffer means "no command yet", not an error."""
    args, rest = resp.parse_command(b"")
    assert args is None
    assert rest == b""


def test_parse_command_rejects_a_non_array_leading_byte():
    """Anything other than a leading '*' is a protocol violation."""
    with pytest.raises(resp.ProtocolError):
        resp.parse_command(b"PING\r\n")


def test_parse_command_rejects_a_bad_array_length():
    """A non-integer array length is a protocol violation."""
    with pytest.raises(resp.ProtocolError):
        resp.parse_command(b"*x\r\n")


def test_encode_simple_string():
    assert resp.encode_simple_string("OK") == b"+OK\r\n"


def test_encode_error():
    assert resp.encode_error("ERR boom") == b"-ERR boom\r\n"


def test_encode_integer():
    assert resp.encode_integer(42) == b":42\r\n"
    assert resp.encode_integer(-1) == b":-1\r\n"


def test_encode_bulk_string():
    assert resp.encode_bulk_string("hello") == b"$5\r\nhello\r\n"


def test_encode_bulk_string_none_is_the_null_bulk_string():
    assert resp.encode_bulk_string(None) == b"$-1\r\n"


def test_encode_array():
    items = [resp.encode_bulk_string("a"), resp.encode_integer(1)]
    assert resp.encode_array(items) == b"*2\r\n$1\r\na\r\n:1\r\n"


def test_encode_array_none_is_the_null_array():
    assert resp.encode_array(None) == b"*-1\r\n"


def test_roundtrip_matches_what_redis_cli_actually_sends():
    """A real redis-cli PING is *1\\r\\n$4\\r\\nPING\\r\\n - confirm that exact shape parses."""
    args, rest = resp.parse_command(b"*1\r\n$4\r\nPING\r\n")
    assert args == ["PING"]
    assert rest == b""
