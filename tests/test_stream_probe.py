import json

from openframetap.video.stream_probe import parse_ffprobe, redact_probe_metadata


def test_h264_metadata_with_audio() -> None:
    parsed = parse_ffprobe(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "profile": "High",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "30000/1001",
                    "r_frame_rate": "30000/1001",
                    "pix_fmt": "yuv420p",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "48000",
                    "channels": 2,
                },
            ],
            "format": {"format_name": "flv", "bit_rate": "6000000"},
        }
    )
    assert parsed["video"]["codec"] == "h264"
    assert parsed["video"]["average_frame_rate"] == 30000 / 1001
    assert parsed["audio"]["codec"] == "aac"


def test_hevc_metadata_and_no_audio() -> None:
    parsed = parse_ffprobe(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "profile": "Main",
                    "width": 3840,
                    "height": 2160,
                    "avg_frame_rate": "25/1",
                }
            ],
            "format": {"format_name": "flv"},
        }
    )
    assert parsed["video"]["codec"] == "hevc"
    assert parsed["audio"] is None
    assert parsed["unknown_streams"] == []


def test_nested_probe_metadata_redacts_stream_key() -> None:
    key = "sensitive-stream-key"
    value = {
        "format": {
            "raw": {"filename": f"rtmp://192.168.1.229:1935/live/{key}"}
        },
        "nested": [f"prefix-{key}-suffix"],
    }
    redacted = redact_probe_metadata(value, {key: "<redacted>"})
    rendered = json.dumps(redacted)
    assert key not in rendered
    assert rendered.count("<redacted>") == 2
