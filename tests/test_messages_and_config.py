"""Tests for src/network/messages.py and src/config/store.py (fixed bugs)."""

from __future__ import annotations

import json
import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.network.messages import normalize_json, parse_json
from src.config.store import ConfigStore


class TestParseJson:
    def test_valid_object(self):
        result = parse_json('{"type": "ping", "seq": 1}')
        assert result == {"type": "ping", "seq": 1}

    def test_empty_string_returns_empty_dict(self):
        assert parse_json("") == {}

    def test_whitespace_string_returns_empty_dict(self):
        assert parse_json("   ") == {}

    def test_malformed_json_returns_empty_dict(self):
        assert parse_json("{not valid json}") == {}

    def test_json_array_wrapped_in_value_key(self):
        result = parse_json("[1, 2, 3]")
        assert result == {"value": [1, 2, 3]}

    def test_json_scalar_wrapped_in_value_key(self):
        result = parse_json("42")
        assert result == {"value": 42}

    def test_nested_object(self):
        payload = '{"a": {"b": 1}}'
        result = parse_json(payload)
        assert result["a"] == {"b": 1}


class TestNormalizeJson:
    def test_produces_compact_sorted_json(self):
        data = {"z": 1, "a": 2}
        result = normalize_json(data)
        assert result == '{"a":2,"z":1}'

    def test_round_trips(self):
        data = {"host": "0.0.0.0", "port": 8765}
        assert json.loads(normalize_json(data)) == data


class TestConfigStore:
    def test_load_missing_file_returns_defaults(self):
        store = ConfigStore("/nonexistent/path/config.json")
        cfg = store.load()
        assert cfg.mode == "master_worker"
        assert cfg.network["server_port"] == 8765

    def test_load_parses_json_file(self):
        payload = {"mode": "worker", "network": {"server_port": 9999}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(payload, fh)
            path = fh.name

        try:
            store = ConfigStore(path)
            cfg = store.load()
            assert cfg.mode == "worker"
            assert cfg.network["server_port"] == 9999
        finally:
            os.unlink(path)

    def test_load_malformed_json_returns_defaults(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as fh:
            fh.write("{bad json here}")
            path = fh.name

        try:
            store = ConfigStore(path)
            cfg = store.load()
            assert cfg.mode == "master_worker"  # default unchanged
        finally:
            os.unlink(path)

    def test_load_merges_partial_config(self):
        payload = {"render": {"overlap_percent": 7.5}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(payload, fh)
            path = fh.name

        try:
            store = ConfigStore(path)
            cfg = store.load()
            # Overridden
            assert cfg.render["overlap_percent"] == 7.5
            # Unchanged defaults still present
            assert cfg.render["max_retries"] == 3
        finally:
            os.unlink(path)
