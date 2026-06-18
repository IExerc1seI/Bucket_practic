"""Tests for BronzeSidecar model."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from geo_bronze.models.sidecar import BronzeSidecar, TileRecord


def make_sidecar(**kwargs: object) -> BronzeSidecar:
    defaults = dict(
        source_id="src",
        request_url="https://example.com",
        request_method="GET",
        request_params={},
        response_status=200,
        response_headers={},
        timestamp=datetime.now(timezone.utc),
        content_hash=hashlib.sha256(b"data").hexdigest(),
        content_type="application/json",
        content_length=4,
        license="ODbL",
        aoi={"type": "bbox", "bbox": [30.0, 50.0, 31.0, 51.0]},
        agent_version="0.1.0",
        decoder_hint="geojson",
        task_id="t1",
        subtask_id="s1",
    )
    defaults.update(kwargs)  # type: ignore[arg-type]
    return BronzeSidecar(**defaults)  # type: ignore[arg-type]


def test_all_required_fields_present() -> None:
    s = make_sidecar()
    d = json.loads(s.model_dump_json())
    required = [
        "source_id", "request_url", "request_method", "response_status",
        "timestamp", "content_hash", "content_type", "content_length",
        "license", "aoi", "agent_version", "decoder_hint", "task_id", "subtask_id",
    ]
    for f in required:
        assert f in d


def test_content_hash_is_valid_sha256() -> None:
    raw = b"hello"
    expected = hashlib.sha256(raw).hexdigest()
    s = make_sidecar(content_hash=expected)
    assert len(s.content_hash) == 64
    assert s.content_hash == expected


def test_timestamp_is_utc() -> None:
    s = make_sidecar()
    assert s.timestamp.tzinfo is not None
    assert s.timestamp.utcoffset() is not None


def test_requires_manual_georeferencing_default_false() -> None:
    s = make_sidecar()
    assert s.requires_manual_georeferencing is False


def test_bbox_override_serialized() -> None:
    s = make_sidecar(bbox_override=[22.1, 44.3, 40.2, 52.3])
    d = json.loads(s.model_dump_json())
    assert d["bbox_override"] == [22.1, 44.3, 40.2, 52.3]


def test_source_axis_order_serialized() -> None:
    s = make_sidecar(source_axis_order="zyx")
    d = json.loads(s.model_dump_json())
    assert d["source_axis_order"] == "zyx"


def test_tiles_list_serialized() -> None:
    tile = TileRecord(z=8, x=10, y=20, content_hash="abc", size_bytes=512, s3_key="k/8/10/20.pbf")
    s = make_sidecar(tiles=[tile], source_axis_order="zyx")
    d = json.loads(s.model_dump_json())
    assert d["tiles"] is not None
    assert len(d["tiles"]) == 1
    assert d["tiles"][0]["z"] == 8
    assert d["tiles"][0]["x"] == 10
    assert d["tiles"][0]["y"] == 20
    assert d["tiles"][0]["s3_key"] == "k/8/10/20.pbf"


def test_warnings_list_default_empty() -> None:
    s = make_sidecar()
    assert s.warnings == []


def test_warnings_serialized() -> None:
    s = make_sidecar(warnings=["bbox_outside_aoi"])
    d = json.loads(s.model_dump_json())
    assert "bbox_outside_aoi" in d["warnings"]
