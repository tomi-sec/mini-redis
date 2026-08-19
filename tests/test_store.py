import time

import pytest

import store


def test_set_and_get_string_roundtrip():
    s = store.new_store()
    store.set_string(s, "k", "v")
    assert store.get_string(s, "k") == "v"


def test_get_missing_key_returns_none():
    s = store.new_store()
    assert store.get_string(s, "nope") is None


def test_get_on_wrong_type_raises_wrongtype():
    s = store.new_store()
    store.lpush(s, "k", ["a"])
    with pytest.raises(store.CommandError, match="WRONGTYPE"):
        store.get_string(s, "k")


def test_del_removes_keys_and_reports_how_many_existed():
    s = store.new_store()
    store.set_string(s, "a", "1")
    store.set_string(s, "b", "2")
    assert store.delete(s, ["a", "b", "missing"]) == 2
    assert store.get_string(s, "a") is None


def test_exists_counts_present_keys():
    s = store.new_store()
    store.set_string(s, "a", "1")
    assert store.exists(s, ["a", "a", "missing"]) == 2


def test_ttl_seconds_missing_key_is_minus_two():
    s = store.new_store()
    assert store.ttl_seconds(s, "nope") == -2


def test_ttl_seconds_key_without_expiry_is_minus_one():
    s = store.new_store()
    store.set_string(s, "k", "v")
    assert store.ttl_seconds(s, "k") == -1


def test_set_with_expiry_reports_a_positive_ttl():
    s = store.new_store()
    store.set_string(s, "k", "v", expire_at=time.time() + 100)
    ttl = store.ttl_seconds(s, "k")
    assert 95 <= ttl <= 100


def test_expired_key_behaves_as_gone_from_get():
    s = store.new_store()
    store.set_string(s, "k", "v", expire_at=time.time() - 1)
    assert store.get_string(s, "k") is None
    assert "k" not in s["data"]


def test_incr_creates_a_missing_key_starting_from_zero():
    s = store.new_store()
    assert store.incr(s, "counter", 1) == 1
    assert store.incr(s, "counter", 1) == 2


def test_decr_is_incr_with_a_negative_delta():
    s = store.new_store()
    store.set_string(s, "counter", "10")
    assert store.incr(s, "counter", -1) == 9


def test_incr_on_wrong_type_raises_wrongtype():
    s = store.new_store()
    store.hset(s, "k", {"f": "v"})
    with pytest.raises(store.CommandError, match="WRONGTYPE"):
        store.incr(s, "k", 1)


def test_incr_on_non_integer_string_raises():
    s = store.new_store()
    store.set_string(s, "k", "not-a-number")
    with pytest.raises(store.CommandError, match="not an integer"):
        store.incr(s, "k", 1)


def test_lpush_and_rpush_order():
    s = store.new_store()
    store.rpush(s, "l", ["a", "b"])
    store.lpush(s, "l", ["z", "y"])
    assert store.lrange(s, "l", 0, -1) == ["y", "z", "a", "b"]


def test_lpop_and_rpop_take_opposite_ends():
    s = store.new_store()
    store.rpush(s, "l", ["a", "b", "c"])
    assert store.lpop(s, "l") == "a"
    assert store.rpop(s, "l") == "c"
    assert store.lrange(s, "l", 0, -1) == ["b"]


def test_lpop_on_empty_list_returns_none_and_key_is_gone():
    s = store.new_store()
    store.rpush(s, "l", ["only"])
    store.lpop(s, "l")
    assert store.lpop(s, "l") is None
    assert store.exists(s, ["l"]) == 0


def test_lrange_supports_negative_indexes():
    s = store.new_store()
    store.rpush(s, "l", ["a", "b", "c", "d"])
    assert store.lrange(s, "l", -2, -1) == ["c", "d"]
    assert store.lrange(s, "l", 1, -2) == ["b", "c"]


def test_lrange_on_missing_key_is_empty():
    s = store.new_store()
    assert store.lrange(s, "nope", 0, -1) == []


def test_llen_counts_elements():
    s = store.new_store()
    store.rpush(s, "l", ["a", "b", "c"])
    assert store.llen(s, "l") == 3
    assert store.llen(s, "nope") == 0


def test_hset_reports_only_newly_created_fields():
    s = store.new_store()
    assert store.hset(s, "h", {"a": "1", "b": "2"}) == 2
    assert store.hset(s, "h", {"a": "99", "c": "3"}) == 1


def test_hget_hdel_hgetall_roundtrip():
    s = store.new_store()
    store.hset(s, "h", {"a": "1", "b": "2"})
    assert store.hget(s, "h", "a") == "1"
    assert store.hget(s, "h", "missing") is None
    assert store.hdel(s, "h", ["a"]) == 1
    assert store.hgetall(s, "h") == {"b": "2"}


def test_hdel_last_field_removes_the_key():
    s = store.new_store()
    store.hset(s, "h", {"a": "1"})
    store.hdel(s, "h", ["a"])
    assert store.exists(s, ["h"]) == 0


def test_sweep_expired_only_evicts_keys_past_their_expiry():
    s = store.new_store()
    store.set_string(s, "gone", "v", expire_at=time.time() - 1)
    store.set_string(s, "still-fresh", "v", expire_at=time.time() + 100)
    evicted = store.sweep_expired(s, sample_size=10)
    assert evicted == 1
    assert "gone" not in s["data"]
    assert "still-fresh" in s["data"]
