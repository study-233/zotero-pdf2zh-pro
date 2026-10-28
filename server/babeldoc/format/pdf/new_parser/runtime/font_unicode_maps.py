from __future__ import annotations

from collections.abc import Mapping

from babeldoc.format.pdf.new_parser.runtime.cmap_types_runtime import FileUnicodeMap
from babeldoc.format.pdf.new_parser.runtime.to_unicode_parser_runtime import (
    parse_tounicode_stream,
)


def build_simple_unicode_map(
    spec: Mapping[object, object],
    *,
    stream_value,
):
    if "ToUnicode" not in spec:
        return None
    stream = stream_value(spec["ToUnicode"])
    unicode_map = FileUnicodeMap()
    parse_tounicode_stream(stream.get_data(), unicode_map)
    return unicode_map
