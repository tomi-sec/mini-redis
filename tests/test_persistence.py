import commands
import persistence
import resp
import store


def test_load_on_a_missing_file_returns_zero_and_leaves_the_store_empty(tmp_path):
    s = store.new_store()
    replayed = persistence.load(tmp_path / "nope.aof", s)
    assert replayed == 0
    assert s["data"] == {}


def test_append_then_load_replays_every_command(tmp_path):
    aof_path = tmp_path / "appendonly.aof"
    with open(aof_path, "ab") as f:
        persistence.append_command(f, ["SET", "k", "v"])
        persistence.append_command(f, ["INCR", "counter"])
        persistence.append_command(f, ["INCR", "counter"])

    s = store.new_store()
    replayed = persistence.load(aof_path, s)
    assert replayed == 3
    assert store.get_string(s, "k") == "v"
    assert store.get_string(s, "counter") == "2"


def test_load_discards_a_half_written_trailing_entry(tmp_path):
    aof_path = tmp_path / "appendonly.aof"
    whole = resp.encode_array([resp.encode_bulk_string(a) for a in ["SET", "k", "v"]])
    truncated_second_entry = resp.encode_array(
        [resp.encode_bulk_string(a) for a in ["SET", "k2", "v2"]]
    )[:10]
    aof_path.write_bytes(whole + truncated_second_entry)

    s = store.new_store()
    replayed = persistence.load(aof_path, s)
    assert replayed == 1
    assert store.get_string(s, "k") == "v"
    assert store.get_string(s, "k2") is None


def test_replayed_commands_go_through_the_real_dispatcher(tmp_path):
    """Replay uses commands.dispatch, so a hash built via HSET survives a restart too."""
    aof_path = tmp_path / "appendonly.aof"
    with open(aof_path, "ab") as f:
        persistence.append_command(f, ["HSET", "h", "field", "value"])

    s = store.new_store()
    persistence.load(aof_path, s)
    assert commands.dispatch(s, ["HGET", "h", "field"]) == b"$5\r\nvalue\r\n"
