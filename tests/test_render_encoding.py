"""FFmpeg metadata and diagnostics may contain UTF-8 or malformed bytes."""
import sys

import pytest

from reelfactory.render import RenderError, _run


def test_metadata_decodes_utf8_and_replaces_invalid_bytes():
    result = _run([sys.executable, "-c",
                   "import sys; sys.stdout.buffer.write(bytes.fromhex('e0a4b081') + b' signalstats.YAVG=42')"])
    assert result == "र\ufffd signalstats.YAVG=42"


def test_invalid_diagnostic_bytes_preserve_actionable_error():
    with pytest.raises(RenderError, match="bad photo"):
        _run([sys.executable, "-c",
              "import sys; sys.stderr.buffer.write(b'bad photo: '+bytes([129])); sys.exit(1)"])
