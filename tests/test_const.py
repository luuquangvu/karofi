"""Tests for Karofi integration constants."""

from custom_components.karofi.const import (
    SUPPORTED_EVENT_TYPES,
    SUPPORTED_PAYLOAD_KEYS,
    SUPPORTED_PROPERTY_CODES,
    MakiposEventType,
    MakiposPayloadKey,
    MakiposPropertyCode,
)


def test_supported_payload_keys_classify_every_enum_value() -> None:
    """Ensure each payload key is explicitly classified as processed or unprocessed."""
    unprocessed: set[str] = set()

    assert SUPPORTED_PAYLOAD_KEYS.isdisjoint(unprocessed)
    assert SUPPORTED_PAYLOAD_KEYS | unprocessed == {key.value for key in MakiposPayloadKey}


def test_supported_event_types_classify_every_enum_value() -> None:
    """Ensure each event type is explicitly classified as processed."""
    unprocessed: set[str] = set()

    assert SUPPORTED_EVENT_TYPES.isdisjoint(unprocessed)
    assert SUPPORTED_EVENT_TYPES | unprocessed == {
        event_type.value for event_type in MakiposEventType
    }


def test_supported_property_codes_classify_every_enum_value() -> None:
    """Ensure each property code is explicitly classified as processed or unprocessed."""
    unprocessed = {
        MakiposPropertyCode.IRO_RSFT.value,
    }

    assert SUPPORTED_PROPERTY_CODES.isdisjoint(unprocessed)
    assert SUPPORTED_PROPERTY_CODES | unprocessed == {
        property_code.value for property_code in MakiposPropertyCode
    }
