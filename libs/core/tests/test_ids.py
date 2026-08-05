"""Identifier generation and parsing."""

from __future__ import annotations

import pytest

from marsool_core.ids import (
    PREFIX_ORDER,
    has_prefix,
    new_id,
    new_ulid,
    parse_id,
    timestamp_ms,
)


def test_new_id_has_prefix_and_ulid_payload() -> None:
    identifier = new_id(PREFIX_ORDER)
    prefix, ulid = parse_id(identifier)
    assert prefix == "ord"
    assert len(ulid) == 26


def test_ids_are_unique() -> None:
    assert len({new_id("usr") for _ in range(2_000)}) == 2_000


def test_ids_sort_chronologically() -> None:
    early = new_id("ord", now_ms=1_700_000_000_000)
    late = new_id("ord", now_ms=1_800_000_000_000)
    # Lexicographic ordering must match time ordering so that keyset pagination on the
    # primary key returns rows in creation order.
    assert early < late


def test_timestamp_roundtrips() -> None:
    assert timestamp_ms(new_id("ord", now_ms=1_723_000_000_123)) == 1_723_000_000_123


@pytest.mark.parametrize("prefix", ["", "has_underscore"])
def test_invalid_prefix_rejected(prefix: str) -> None:
    with pytest.raises(ValueError, match="invalid id prefix"):
        new_id(prefix)


@pytest.mark.parametrize("value", ["nounderscore", "_abc", "ord_short", "ord_" + "U" * 26])
def test_malformed_identifiers_rejected(value: str) -> None:
    # 'U' is not in the Crockford alphabet, so the last case has the right length but an
    # invalid payload.
    with pytest.raises(ValueError, match="malformed"):
        parse_id(value)


def test_has_prefix_is_total() -> None:
    assert has_prefix(new_id("mch"), "mch")
    assert not has_prefix(new_id("mch"), "usr")
    assert not has_prefix("garbage", "usr")


def test_new_ulid_rejects_out_of_range_timestamp() -> None:
    with pytest.raises(ValueError, match="out of ULID range"):
        new_ulid(now_ms=2**48)
