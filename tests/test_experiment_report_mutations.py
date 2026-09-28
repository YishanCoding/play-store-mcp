"""Contract v3 oracle for the saved, real experiment report wire payload."""

from __future__ import annotations

import base64
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from play_store_mcp.client import PlayStoreClient, PlayStoreClientError

EXPERIMENT_ID = "9063393730453672176"
FIXTURE = Path(__file__).parent / "fixtures" / "experiment_report_real.json"

# Contract C3, measured independently from the real wire response. Neither
# the parser nor a runtime discovery pass determines this table.
MESSAGE_COUNTS = {
    (2,): 1,
    (2, 3): 3,
    (4,): 1,
    (4, 1): 1,
    (4, 7): 1,
    (5,): 2,
    (5, 6): 2,
    (5, 6, 1): 14,
    (9,): 1,
    (9, 4): 1,
    (9, 5): 1,
    (9, 6): 3,
    (9, 6, 1): 21,
}


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise AssertionError("invalid oracle seed: truncated varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise AssertionError("invalid oracle seed: oversized varint")


def _varint(value: int) -> bytes:
    output = bytearray()
    while value >= 128:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


def _fields(data: bytes) -> list[tuple[int, int, bytes]]:
    """Read wire fields independently; retain raw values for exact rewriting."""
    fields = []
    offset = 0
    while offset < len(data):
        tag, offset = _read_varint(data, offset)
        number, wire = tag >> 3, tag & 7
        assert number > 0
        if wire == 2:
            length, offset = _read_varint(data, offset)
            value = data[offset : offset + length]
            assert len(value) == length
            offset += length
        elif wire == 0:
            start = offset
            _, offset = _read_varint(data, offset)
            value = data[start:offset]
        elif wire in (1, 5):
            length = 8 if wire == 1 else 4
            value = data[offset : offset + length]
            assert len(value) == length
            offset += length
        else:
            raise AssertionError(f"invalid oracle seed wire type {wire}")
        fields.append((number, wire, value))
    return fields


def _encode_field(number: int, wire: int, value: bytes) -> bytes:
    tag = _varint((number << 3) | wire)
    return tag + (_varint(len(value)) if wire == 2 else b"") + value


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def _payload() -> bytes:
    return base64.b64decode(_fixture()["1"]["2"], validate=True)


def _response(payload: bytes) -> dict[str, Any]:
    envelope = _fixture()
    envelope["1"]["2"] = base64.b64encode(payload).decode("ascii")
    return envelope


def _paths(data: bytes, prefix: tuple[int, ...] = ()) -> Counter[tuple[int, ...]]:
    found: Counter[tuple[int, ...]] = Counter()
    for number, wire, value in _fields(data):
        path = (*prefix, number)
        if path in MESSAGE_COUNTS:
            assert wire == 2, f"oracle seed path {path} is not length-delimited"
            found[path] += 1
        if wire == 2 and any(p[: len(path)] == path and len(p) > len(path) for p in MESSAGE_COUNTS):
            found.update(_paths(value, path))
    return found


def _urls_by_top(data: bytes, prefix: tuple[int, ...] = ()) -> dict[int, set[bytes]]:
    """Collect raw URL fields by top-level field, without the parser."""
    found: dict[int, set[bytes]] = {}
    for number, wire, value in _fields(data):
        if wire != 2:
            continue
        current = (*prefix, number)
        if value.startswith(b"https://"):
            found.setdefault(current[0], set()).add(value)
        else:
            try:
                for top, urls in _urls_by_top(value, current).items():
                    found.setdefault(top, set()).update(urls)
            except AssertionError:
                # A text/opaque leaf is not a nested wire message.
                pass
    return found


def _mutate(data: bytes, path: tuple[int, ...], occurrence: int, kind: str) -> bytes:
    seen = 0

    def rewrite(message: bytes, prefix: tuple[int, ...]) -> bytes:
        nonlocal seen
        output = bytearray()
        for number, wire, value in _fields(message):
            current = (*prefix, number)
            if current == path:
                if seen == occurrence:
                    seen += 1
                    if kind == "delete":
                        continue
                    if kind == "varint":
                        wire, value = 0, b"\x01"
                    else:
                        wire = 2
                        value = {
                            "newline": b"\x0a",
                            "printable": b"(",
                            "empty": b"",
                            "short_message": b"\x0a\x00",
                        }[kind]
                else:
                    seen += 1
            elif current == path[: len(current)] and wire == 2:
                value = rewrite(value, current)
            output.extend(_encode_field(number, wire, value))
        return bytes(output)

    result = rewrite(data, ())
    assert seen > occurrence, f"oracle could not locate {path} #{occurrence}"
    assert result != data
    return result


def _parse(payload: bytes):
    return PlayStoreClient.parse_experiment_report_startup(_response(payload), EXPERIMENT_ID)


def test_experiment_report_real_fixture() -> None:
    assert _paths(_payload()) == Counter(MESSAGE_COUNTS)
    report = _parse(_payload())
    assert [(v.name, v.audience_percent) for v in report.variants] == [
        ("Current listing", 34),
        ("7-1 last (1,2,3,4,5,6,7-1)", 33),
        ("7-1 third (1,2,7-1,3,4,5,6)", 33),
    ]
    assert {top: len(urls) for top, urls in _urls_by_top(_payload()).items()} == {5: 7, 9: 23}
    assert len(report.image_urls) == 30


def test_c3_mutation_oracle() -> None:
    """Every one of 52 message occurrences rejects each of three wrong types."""
    accepted: Counter[tuple[int, ...]] = Counter()
    total = 0
    for path, count in MESSAGE_COUNTS.items():
        for occurrence in range(count):
            for kind in ("newline", "printable", "varint"):
                total += 1
                changed = _mutate(_payload(), path, occurrence, kind)
                try:
                    _parse(changed)
                except PlayStoreClientError:
                    pass
                else:
                    accepted[path] += 1
    assert total == 156
    by_path = {"/".join(map(str, p)): accepted[p] for p in MESSAGE_COUNTS}
    print(f"C3 accepted mutations by path: {by_path}")
    assert sum(accepted.values()) == 0, by_path


@pytest.mark.parametrize("kind", ["delete", "empty", "short_message"])
def test_n1_complete_message_control(kind: str) -> None:
    # Other images remain, so shorter but well-formed lists are valid.
    assert _parse(_mutate(_payload(), (9, 6, 1), 0, kind)).image_urls


def test_report_image_group_truncated_newline() -> None:
    with pytest.raises(PlayStoreClientError):
        _parse(_mutate(_payload(), (9, 6), 0, "newline"))


def test_report_image_group_truncated_printable() -> None:
    with pytest.raises(PlayStoreClientError):
        _parse(_mutate(_payload(), (9, 6), 0, "printable"))


def test_report_field9_replaced_printable() -> None:
    with pytest.raises(PlayStoreClientError):
        _parse(_mutate(_payload(), (9,), 0, "printable"))
