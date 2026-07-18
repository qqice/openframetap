from pathlib import Path
from types import SimpleNamespace

from openframetap.ble_scan import advertisement_to_record, load_replay, scan_payload
from openframetap.profiles import classify_dji_osmo


def test_parser_preserves_manufacturer_and_raw_fields() -> None:
    device = SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF",
        name=None,
        details={"props": {"AddressType": "random"}},
    )
    advertisement = SimpleNamespace(
        local_name=None,
        rssi=-41,
        tx_power=-8,
        service_uuids=["0000FFF0-0000-1000-8000-00805F9B34FB"],
        manufacturer_data={0x0137: bytes.fromhex("0102a0ff")},
        service_data={"1234": bytes.fromhex("beef")},
        platform_data=(bytes.fromhex("aabb"), {"source": "fixture"}),
    )

    record = advertisement_to_record(device, advertisement, observed_at="fixed")

    assert record.address_type == "random"
    assert record.manufacturer_data == {"0x0137": "0102a0ff"}
    assert record.service_data == {"1234": "beef"}
    assert record.raw_advertisement_fields["platform_data"][0] == "aabb"
    assert record.suspected_dji_osmo is True
    assert record.suspected_model_raw_hex == "0102a0ff"


def test_unknown_dji_model_is_tolerated_without_model_claim() -> None:
    match = classify_dji_osmo(
        {
            "display_name": None,
            "service_uuids": ["0000fff0-0000-1000-8000-00805f9b34fb"],
            "manufacturer_data": {"0x08aa": "ffffffffffffffff"},
        }
    )
    assert match.suspected is True
    assert match.model_raw_hex == "ffffffffffffffff"
    assert not any(reason.startswith("model-") for reason in match.reasons)


def test_saved_fixture_replay() -> None:
    records = load_replay(Path("tests/fixtures/advertisements.json"))
    payload = scan_payload(records, seconds=None)
    assert payload["device_count"] == 2
    assert payload["suspected_dji_count"] == 1
    assert payload["devices"][0]["manufacturer_data"]["0x0137"] == "deadc0de01020304"
