from openframetap.video.stream_probe import parse_ffprobe


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
