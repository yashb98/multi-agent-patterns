"""Tests for structural content hashing."""
from __future__ import annotations

import pytest

from jobpulse.content_hasher import compute_content_hash


class TestContentHasher:
    def test_same_fields_same_hash(self):
        fields_a = [
            {"label": "First Name", "type": "text"},
            {"label": "Email", "type": "text"},
            {"label": "Resume", "type": "file"},
        ]
        fields_b = [
            {"label": "First Name", "type": "text"},
            {"label": "Email", "type": "text"},
            {"label": "Resume", "type": "file"},
        ]
        assert compute_content_hash(fields_a) == compute_content_hash(fields_b)

    def test_different_fields_different_hash(self):
        fields_a = [{"label": "First Name", "type": "text"}]
        fields_b = [{"label": "Salary", "type": "text"}]
        assert compute_content_hash(fields_a) != compute_content_hash(fields_b)

    def test_order_independent(self):
        fields_a = [
            {"label": "Email", "type": "text"},
            {"label": "Name", "type": "text"},
        ]
        fields_b = [
            {"label": "Name", "type": "text"},
            {"label": "Email", "type": "text"},
        ]
        assert compute_content_hash(fields_a) == compute_content_hash(fields_b)

    def test_ignores_non_structural_keys(self):
        fields_a = [{"label": "Name", "type": "text", "value": "Yash", "selector": "#name"}]
        fields_b = [{"label": "Name", "type": "text", "value": "", "selector": ".name-input"}]
        assert compute_content_hash(fields_a) == compute_content_hash(fields_b)

    def test_includes_type_in_hash(self):
        fields_a = [{"label": "Gender", "type": "text"}]
        fields_b = [{"label": "Gender", "type": "radio"}]
        assert compute_content_hash(fields_a) != compute_content_hash(fields_b)

    def test_empty_fields_returns_hash(self):
        h = compute_content_hash([])
        assert isinstance(h, str)
        assert len(h) == 16

    def test_hash_is_hex_prefix(self):
        h = compute_content_hash([{"label": "X", "type": "text"}])
        assert len(h) == 16
        int(h, 16)  # should not raise
