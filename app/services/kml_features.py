"""Shared KML classification before agricultural polygon interpretation."""

import re
from html import unescape

KML_NAMESPACES = {"k": "http://www.opengis.net/kml/2.2"}


def is_weir_polygon(placemark, hints=None):
    if not placemark.findall(".//k:Polygon", KML_NAMESPACES):
        return False
    description = placemark.findtext("k:description", "", KML_NAMESPACES)
    tokens = re.findall(r"[A-Z0-9]+", re.sub(r"<[^>]+>", " ", unescape(description)).upper())
    name = " ".join(placemark.findtext("k:name", "", KML_NAMESPACES).split()).casefold()
    hint = str((hints or {}).get(name, "")).strip().casefold()
    return "WR" in tokens or hint in {"wr", "weir"}
