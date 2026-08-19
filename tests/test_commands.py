import commands
import store


def test_ping_with_no_argument_replies_pong():
    s = store.new_store()
    assert commands.dispatch(s, ["PING"]) == b"+PONG\r\n"


def test_ping_with_an_argument_echoes_it_as_a_bulk_string():
    s = store.new_store()
    assert commands.dispatch(s, ["PING", "hello"]) == b"$5\r\nhello\r\n"


def test_echo():
    s = store.new_store()
    assert commands.dispatch(s, ["ECHO", "hi"]) == b"$2\r\nhi\r\n"


def test_command_is_a_valid_empty_array_so_redis_cli_does_not_choke():
    s = store.new_store()
    assert commands.dispatch(s, ["COMMAND"]) == b"*0\r\n"


def test_command_dispatch_is_case_insensitive():
    s = store.new_store()
    assert commands.dispatch(s, ["ping"]) == b"+PONG\r\n"


def test_unknown_command_is_a_resp_error_not_an_exception():
    s = store.new_store()
    reply = commands.dispatch(s, ["NOPE"])
    assert reply.startswith(b"-ERR unknown command")


def test_get_on_a_missing_key_is_the_null_bulk_string():
    s = store.new_store()
    assert commands.dispatch(s, ["GET", "nope"]) == b"$-1\r\n"


def test_set_then_get_roundtrip():
    s = store.new_store()
    assert commands.dispatch(s, ["SET", "k", "v"]) == b"+OK\r\n"
    assert commands.dispatch(s, ["GET", "k"]) == b"$1\r\nv\r\n"


def test_set_with_ex_gives_the_key_a_positive_ttl():
    s = store.new_store()
    commands.dispatch(s, ["SET", "k", "v", "EX", "100"])
    reply = commands.dispatch(s, ["TTL", "k"])
    ttl = int(reply[1:-2])  # strip RESP's leading ":" and trailing "\r\n"
    assert 90 <= ttl <= 100


def test_set_with_bad_option_is_a_syntax_error():
    s = store.new_store()
    reply = commands.dispatch(s, ["SET", "k", "v", "BOGUS"])
    assert reply == b"-ERR syntax error\r\n"


def test_get_on_wrong_type_matches_real_redis_error_style():
    s = store.new_store()
    commands.dispatch(s, ["LPUSH", "k", "v"])
    reply = commands.dispatch(s, ["GET", "k"])
    assert reply.startswith(b"-WRONGTYPE")


def test_del_and_exists():
    s = store.new_store()
    commands.dispatch(s, ["SET", "a", "1"])
    assert commands.dispatch(s, ["EXISTS", "a", "b"]) == b":1\r\n"
    assert commands.dispatch(s, ["DEL", "a", "b"]) == b":1\r\n"


def test_incr_and_decr():
    s = store.new_store()
    assert commands.dispatch(s, ["INCR", "c"]) == b":1\r\n"
    assert commands.dispatch(s, ["INCR", "c"]) == b":2\r\n"
    assert commands.dispatch(s, ["DECR", "c"]) == b":1\r\n"


def test_incr_on_non_integer_is_a_resp_error():
    s = store.new_store()
    commands.dispatch(s, ["SET", "c", "abc"])
    reply = commands.dispatch(s, ["INCR", "c"])
    assert reply.startswith(b"-ERR value is not an integer")


def test_wrong_arity_is_a_resp_error():
    s = store.new_store()
    reply = commands.dispatch(s, ["GET"])
    assert reply.startswith(b"-ERR wrong number of arguments")


def test_lpush_rpush_lrange_with_negative_indexes():
    s = store.new_store()
    commands.dispatch(s, ["RPUSH", "l", "a", "b", "c"])
    reply = commands.dispatch(s, ["LRANGE", "l", "-2", "-1"])
    assert reply == b"*2\r\n$1\r\nb\r\n$1\r\nc\r\n"


def test_llen_and_lpop():
    s = store.new_store()
    commands.dispatch(s, ["RPUSH", "l", "a", "b"])
    assert commands.dispatch(s, ["LLEN", "l"]) == b":2\r\n"
    assert commands.dispatch(s, ["LPOP", "l"]) == b"$1\r\na\r\n"


def test_hset_hget_hdel():
    s = store.new_store()
    assert commands.dispatch(s, ["HSET", "h", "f1", "v1", "f2", "v2"]) == b":2\r\n"
    assert commands.dispatch(s, ["HGET", "h", "f1"]) == b"$2\r\nv1\r\n"
    assert commands.dispatch(s, ["HDEL", "h", "f1"]) == b":1\r\n"


def test_hgetall_returns_a_flat_field_value_array():
    s = store.new_store()
    commands.dispatch(s, ["HSET", "h", "f1", "v1"])
    assert commands.dispatch(s, ["HGETALL", "h"]) == b"*2\r\n$2\r\nf1\r\n$2\r\nv1\r\n"


def test_save_with_no_aof_file_in_context_still_replies_ok():
    s = store.new_store()
    assert commands.dispatch(s, ["SAVE"], context={}) == b"+OK\r\n"
