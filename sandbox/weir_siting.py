from __future__ import annotations
from pathlib import Path
from textwrap import dedent


import argparse
import html
import math
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

import folium
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from pyproj import CRS, Transformer

out = Path("/mnt/data/weir_siting")
out.mkdir(exist_ok=True)

#!/usr/bin/env python3
"""
Screen candidate weir locations along a KML river centreline.

What this does
--------------
1. Reads a LineString from KML.
2. Samples a DEM along the line (OpenTopoData SRTM 30 m by default, or a local GeoTIFF).
3. Orients the line from upstream to downstream.
4. For each possible weir position, models the contiguous *upstream channel* with bed
   elevation at or below the proposed crest elevation.
5. Ranks sites by the ponded channel length and writes maps, plots, CSV and KML outputs.

This is a 1-D desktop screening method, not a flood, dam-safety, geotechnical, or
environmental approval design. It deliberately does not estimate 2-D flood extent,
storage volume, spillway capacity, land ownership, geology, ecological flow, or
downstream consequences.

For a 1.5 m weir, a 30 m DEM is generally only suitable for finding areas worth
surveying. Use surveyed channel levels or a high-resolution bare-earth DEM before
making decisions.
"""


KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}


def parse_kml_linestring(kml_path: Path, placemark_name: str | None = None) -> np.ndarray:
    """Return KML LineString vertices as [longitude, latitude, altitude]."""
    root = ET.parse(kml_path).getroot()
    candidates = []
    for placemark in root.findall(".//kml:Placemark", KML_NS):
        name = placemark.findtext("kml:name", default="", namespaces=KML_NS)
        coordinates = placemark.find(".//kml:LineString/kml:coordinates", KML_NS)
        if coordinates is not None and coordinates.text:
            candidates.append((name, coordinates.text))

    if not candidates:
        raise ValueError("No KML LineString found.")

    if placemark_name:
        matched = [(name, txt) for name, txt in candidates if name == placemark_name]
        if not matched:
            options = ", ".join(repr(name) for name, _ in candidates)
            raise ValueError(f"No LineString named {placemark_name!r}. Available: {options}")
        _, coordinate_text = matched[0]
    else:
        if len(candidates) > 1:
            print(f"Multiple LineStrings found; using {candidates[0][0]!r}. "
                  "Use --placemark-name to choose another one.")
        _, coordinate_text = candidates[0]

    rows = []
    for token in coordinate_text.replace("\n", " ").split():
        values = token.split(",")
        if len(values) < 2:
            continue
        lon, lat = float(values[0]), float(values[1])
        alt = float(values[2]) if len(values) >= 3 and values[2] else np.nan
        rows.append((lon, lat, alt))

    if len(rows) < 2:
        raise ValueError("The LineString contains fewer than two valid coordinates.")
    return np.asarray(rows, dtype=float)


def utm_crs_for(lon: float, lat: float) -> CRS:
    """Choose a local UTM CRS from a WGS84 longitude/latitude."""
    zone = int((lon + 180) // 6) + 1
    epsg = (32600 if lat >= 0 else 32700) + zone
    return CRS.from_epsg(epsg)


def densify_line(vertices: np.ndarray, spacing_m: float) -> pd.DataFrame:
    """Densify the centreline in UTM metres and return WGS84 points with chainage."""
    lon0, lat0 = float(vertices[:, 0].mean()), float(vertices[:, 1].mean())
    utm = utm_crs_for(lon0, lat0)
    to_utm = Transformer.from_crs("EPSG:4326", utm, always_xy=True)
    to_wgs = Transformer.from_crs(utm, "EPSG:4326", always_xy=True)

    x, y = to_utm.transform(vertices[:, 0], vertices[:, 1])
    xy = np.column_stack([x, y])
    segment_len = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    source_chainage = np.concatenate([[0.0], np.cumsum(segment_len)])
    total = float(source_chainage[-1])

    if total <= 0:
        raise ValueError("The river centreline has zero length.")

    target_chainage = np.arange(0.0, total, spacing_m)
    if target_chainage[-1] != total:
        target_chainage = np.append(target_chainage, total)

    x_new = np.interp(target_chainage, source_chainage, xy[:, 0])
    y_new = np.interp(target_chainage, source_chainage, xy[:, 1])
    lon_new, lat_new = to_wgs.transform(x_new, y_new)

    return pd.DataFrame(
        {
            "chainage_m": target_chainage,
            "x_m": x_new,
            "y_m": y_new,
            "lon": lon_new,
            "lat": lat_new,
        }
    )


def sample_opentopodata(
    lons: Iterable[float],
    lats: Iterable[float],
    dataset: str,
    batch_size: int = 100,
) -> np.ndarray:
    """
    Sample the public OpenTopoData endpoint.

    The public service documents a maximum of 100 locations per request and roughly
    one request per second. This function respects both limits.
    """
    lons = np.asarray(list(lons), dtype=float)
    lats = np.asarray(list(lats), dtype=float)
    endpoint = f"https://api.opentopodata.org/v1/{dataset}"
    elevations: list[float] = []

    for start in range(0, len(lons), batch_size):
        stop = min(start + batch_size, len(lons))
        locations = "|".join(f"{lat:.7f},{lon:.7f}" for lat, lon in zip(lats[start:stop], lons[start:stop]))
        payload = {"locations": locations, "interpolation": "bilinear"}

        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = requests.post(endpoint, json=payload, timeout=60)
                response.raise_for_status()
                data = response.json()
                if data.get("status") != "OK":
                    raise RuntimeError(data.get("error", f"Unexpected API response: {data}"))
                values = [item.get("elevation") for item in data["results"]]
                if len(values) != stop - start:
                    raise RuntimeError("DEM API returned the wrong number of samples.")
                elevations.extend(np.nan if value is None else float(value) for value in values)
                last_error = None
                break
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                time.sleep(2 ** attempt)

        if last_error is not None:
            raise RuntimeError(
                "Could not query OpenTopoData. Supply a local GeoTIFF using --dem instead. "
                f"Last error: {last_error}"
            )
        if stop < len(lons):
            time.sleep(1.05)

    return np.asarray(elevations, dtype=float)


def sample_local_dem(dem_path: Path, lons: Iterable[float], lats: Iterable[float]) -> np.ndarray:
    """Sample a local DEM raster at WGS84 points. Requires rasterio."""
    try:
        import rasterio
        from rasterio.warp import transform
    except ImportError as exc:
        raise RuntimeError(
            "Local DEM sampling requires rasterio. Install it with: pip install rasterio"
        ) from exc

    lons = np.asarray(list(lons), dtype=float)
    lats = np.asarray(list(lats), dtype=float)
    with rasterio.open(dem_path) as src:
        xs, ys = transform("EPSG:4326", src.crs, lons.tolist(), lats.tolist())
        sampled = np.asarray([value[0] for value in src.sample(zip(xs, ys))], dtype=float)
        if src.nodata is not None:
            sampled[np.isclose(sampled, src.nodata)] = np.nan
    return sampled


def smooth_profile(elevation_m: np.ndarray, spacing_m: float, smoothing_window_m: float) -> np.ndarray:
    """Median smooth a DEM profile without changing its overall position."""
    if smoothing_window_m <= 0:
        return elevation_m.copy()

    window = max(3, int(round(smoothing_window_m / spacing_m)))
    if window % 2 == 0:
        window += 1
    smoothed = pd.Series(elevation_m).rolling(window, center=True, min_periods=1).median().to_numpy()
    return smoothed


def orient_upstream_to_downstream(profile: pd.DataFrame, flow_direction: str) -> tuple[pd.DataFrame, bool]:
    """
    Return upstream-to-downstream order.

    With --flow-direction auto, the higher endpoint is assumed to be upstream.
    Override this if field knowledge says otherwise.
    """
    if flow_direction not in {"auto", "start-to-end", "end-to-start"}:
        raise ValueError("flow_direction must be auto, start-to-end or end-to-start")

    if flow_direction == "start-to-end":
        reverse = False
    elif flow_direction == "end-to-start":
        reverse = True
    else:
        reverse = profile["elevation_smooth_m"].iloc[0] < profile["elevation_smooth_m"].iloc[-1]

    result = profile.iloc[::-1].reset_index(drop=True) if reverse else profile.copy().reset_index(drop=True)
    original_length = float(result["chainage_m"].max())
    result["chainage_m"] = original_length - result["chainage_m"].iloc[0] - result["chainage_m"]
    # After reversal above chainage is descending; replace it with increasing upstream chainage.
    dx = np.diff(result["x_m"].to_numpy())
    dy = np.diff(result["y_m"].to_numpy())
    result["chainage_m"] = np.concatenate([[0.0], np.cumsum(np.hypot(dx, dy))])
    return result, reverse


def compute_candidate(profile: pd.DataFrame, dam_idx: int, weir_height_m: float) -> dict:
    """
    One-dimensional connected-channel inundation.

    Water backs up from the dam only until the first upstream profile point higher than
    the water surface. This intentionally prevents a low point beyond a higher sill
    from being counted as connected storage.
    """
    z = profile["elevation_smooth_m"].to_numpy()
    s = profile["chainage_m"].to_numpy()

    crest = float(z[dam_idx] + weir_height_m)
    upstream_higher = np.flatnonzero(z[:dam_idx] > crest)
    start_idx = int(upstream_higher[-1] + 1) if upstream_higher.size else 0

    inundated_slice = slice(start_idx, dam_idx + 1)
    flooded_z = z[inundated_slice]
    flooded_s = s[inundated_slice]
    depths = np.clip(crest - flooded_z, 0.0, None)
    length = float(flooded_s[-1] - flooded_s[0])

    # Along-centreline depth measures are only a screening proxy, not storage volume.
    mean_depth = float(np.trapezoid(depths, flooded_s) / length) if length > 0 else 0.0
    return {
        "dam_idx": int(dam_idx),
        "water_start_idx": start_idx,
        "crest_elevation_m": crest,
        "ponded_length_m": length,
        "mean_centreline_depth_m": mean_depth,
        "max_centreline_depth_m": float(depths.max(initial=0.0)),
        "upstream_bed_rise_m": float(z[start_idx] - z[dam_idx]),
    }


def find_candidates(
    profile: pd.DataFrame,
    weir_height_m: float,
    min_ponded_length_m: float,
    edge_exclusion_m: float,
) -> pd.DataFrame:
    """Evaluate each sample point as a possible dam location."""
    s = profile["chainage_m"].to_numpy()
    candidates = []
    for idx in range(1, len(profile) - 1):
        if s[idx] < edge_exclusion_m or (s[-1] - s[idx]) < edge_exclusion_m:
            continue
        candidate = compute_candidate(profile, idx, weir_height_m)
        if candidate["ponded_length_m"] >= min_ponded_length_m:
            row = profile.iloc[idx]
            candidate.update(
                {
                    "dam_chainage_m": float(row.chainage_m),
                    "lon": float(row.lon),
                    "lat": float(row.lat),
                    "bed_elevation_m": float(row.elevation_smooth_m),
                }
            )
            candidates.append(candidate)

    if not candidates:
        return pd.DataFrame()
    return pd.DataFrame(candidates).sort_values(
        ["ponded_length_m", "mean_centreline_depth_m"], ascending=False
    ).reset_index(drop=True)


def choose_spaced_sites(candidates: pd.DataFrame, min_separation_m: float, n_sites: int) -> pd.DataFrame:
    """Keep the best candidates while collapsing nearby alternatives into one site."""
    kept = []
    for _, candidate in candidates.iterrows():
        if all(abs(candidate.dam_chainage_m - chosen.dam_chainage_m) >= min_separation_m for chosen in kept):
            kept.append(candidate)
        if len(kept) >= n_sites:
            break

    result = pd.DataFrame(kept).reset_index(drop=True)
    if not result.empty:
        result.insert(0, "rank", np.arange(1, len(result) + 1))
    return result


def candidate_for_requested_position(
    profile: pd.DataFrame,
    weir_height_m: float,
    requested_chainage_m: float | None,
    requested_lonlat: tuple[float, float] | None,
) -> pd.DataFrame:
    """Compute one candidate at a requested chainage or longitude/latitude."""
    if requested_chainage_m is not None:
        idx = int(np.argmin(np.abs(profile["chainage_m"].to_numpy() - requested_chainage_m)))
    elif requested_lonlat is not None:
        lon, lat = requested_lonlat
        # Local UTM coordinates make nearest-point selection meaningful in metres.
        utm = utm_crs_for(float(profile["lon"].mean()), float(profile["lat"].mean()))
        transformer = Transformer.from_crs("EPSG:4326", utm, always_xy=True)
        x, y = transformer.transform(lon, lat)
        dist2 = (profile["x_m"].to_numpy() - x) ** 2 + (profile["y_m"].to_numpy() - y) ** 2
        idx = int(np.argmin(dist2))
    else:
        raise ValueError("Requested position missing.")

    candidate = compute_candidate(profile, idx, weir_height_m)
    row = profile.iloc[idx]
    candidate.update(
        {
            "rank": 1,
            "dam_chainage_m": float(row.chainage_m),
            "lon": float(row.lon),
            "lat": float(row.lat),
            "bed_elevation_m": float(row.elevation_smooth_m),
        }
    )
    return pd.DataFrame([candidate])


def save_profile_plot(profile: pd.DataFrame, selected: pd.Series, output_file: Path, height_m: float) -> None:
    """Plot the full profile and fill the selected connected impoundment."""
    s = profile["chainage_m"].to_numpy()
    z_raw = profile["elevation_m"].to_numpy()
    z = profile["elevation_smooth_m"].to_numpy()
    start_idx, dam_idx = int(selected.water_start_idx), int(selected.dam_idx)
    crest = float(selected.crest_elevation_m)

    fig, ax = plt.subplots(figsize=(13, 6))
    ax.plot(s / 1000, z_raw, linewidth=1.0, alpha=0.45, label="DEM sample")
    ax.plot(s / 1000, z, linewidth=1.7, label="Smoothed channel profile")
    water_s = s[start_idx : dam_idx + 1] / 1000
    water_z = z[start_idx : dam_idx + 1]
    ax.fill_between(water_s, water_z, crest, where=water_z <= crest, alpha=0.55, label="Modelled water along channel")
    ax.axhline(crest, linestyle="--", linewidth=1.2, label=f"Weir crest ({height_m:.2f} m above local bed)")
    ax.axvline(s[dam_idx] / 1000, linestyle=":", linewidth=1.6, label="Weir position")
    ax.set(
        title=f"Selected weir screening profile — ponded channel length: {selected.ponded_length_m / 1000:.2f} km",
        xlabel="Distance downstream from upstream endpoint (km)",
        ylabel="Elevation (m)",
    )
    ax.legend(loc="best")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_file, dpi=180)
    plt.close(fig)


def save_candidates_plot(profile: pd.DataFrame, top_sites: pd.DataFrame, output_file: Path) -> None:
    """Create a geographic plot of ranked locations without relying on a web basemap."""
    fig, ax = plt.subplots(figsize=(10, 9))
    ax.plot(profile["lon"], profile["lat"], linewidth=1.5, alpha=0.65, label="River centreline")
    scatter = ax.scatter(
        top_sites["lon"],
        top_sites["lat"],
        c=top_sites["ponded_length_m"] / 1000,
        s=70,
        marker="o",
        edgecolors="black",
        linewidths=0.5,
        label="Top candidate sites",
    )
    for _, row in top_sites.iterrows():
        ax.annotate(
            str(int(row["rank"])),
            (row["lon"], row["lat"]),
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=8,
            weight="bold",
        )
    colorbar = fig.colorbar(scatter, ax=ax)
    colorbar.set_label("Ponded channel length (km)")
    ax.set(title="Top weir locations ranked by connected ponded channel length", xlabel="Longitude", ylabel="Latitude")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(output_file, dpi=180)
    plt.close(fig)


def add_water_segment(map_object: folium.Map, profile: pd.DataFrame, candidate: pd.Series, name: str, show: bool) -> None:
    """Add a coloured inundated centreline and candidate marker to a Folium map."""
    start, dam = int(candidate.water_start_idx), int(candidate.dam_idx)
    coords = profile.iloc[start : dam + 1][["lat", "lon"]].values.tolist()
    group = folium.FeatureGroup(name=name, show=show)
    folium.PolyLine(coords, color="#1782c5", weight=7, opacity=0.85, tooltip=name).add_to(group)

    tooltip = (
        f"{name}<br>"
        f"Ponded channel: {candidate.ponded_length_m / 1000:.2f} km<br>"
        f"Weir height: {candidate.crest_elevation_m - candidate.bed_elevation_m:.2f} m<br>"
        f"Centreline mean depth: {candidate.mean_centreline_depth_m:.2f} m<br>"
        f"Position: {candidate.lat:.6f}, {candidate.lon:.6f}"
    )
    folium.CircleMarker(
        location=[candidate.lat, candidate.lon],
        radius=7,
        color="#1b1b1b",
        weight=2,
        fill=True,
        fill_color="#f39c12",
        fill_opacity=1.0,
        tooltip=folium.Tooltip(tooltip, sticky=True),
    ).add_to(group)
    group.add_to(map_object)


def save_interactive_map(profile: pd.DataFrame, top_sites: pd.DataFrame, selected: pd.Series, output_file: Path) -> None:
    """Create a browser map with all top sites and the selected detailed scenario."""
    river_coords = profile[["lat", "lon"]].values.tolist()
    centre = [float(profile["lat"].mean()), float(profile["lon"].mean())]
    fmap = folium.Map(location=centre, zoom_start=13, tiles="OpenStreetMap", control_scale=True)
    folium.PolyLine(river_coords, color="#777777", weight=3, opacity=0.75, tooltip="River centreline").add_to(fmap)

    candidates_group = folium.FeatureGroup(name="Top candidate points", show=True)
    for _, row in top_sites.iterrows():
        label = (
            f"Rank {int(row['rank'])}: {row['ponded_length_m'] / 1000:.2f} km<br>"
            f"Lat/Lon: {row['lat']:.6f}, {row['lon']:.6f}"
        )
        folium.CircleMarker(
            location=[row.lat, row.lon],
            radius=5 + min(8, row.ponded_length_m / 700),
            color="#9b59b6",
            weight=1,
            fill=True,
            fill_color="#9b59b6",
            fill_opacity=0.85,
            tooltip=folium.Tooltip(label, sticky=True),
        ).add_to(candidates_group)
    candidates_group.add_to(fmap)

    add_water_segment(fmap, profile, selected, "Selected weir: modelled inundated centreline", show=True)
    for _, row in top_sites.iterrows():
        add_water_segment(
            fmap,
            profile,
            row,
            f"Rank {int(row['rank'])} waterline ({row['ponded_length_m'] / 1000:.2f} km)",
            show=False,
        )
    folium.LayerControl(collapsed=False).add_to(fmap)
    fmap.save(output_file)


def write_results_kml(profile: pd.DataFrame, top_sites: pd.DataFrame, selected: pd.Series, output_file: Path) -> None:
    """Write ranked points and inundated channel lines for Google Earth."""
    placemarks = []
    for _, row in top_sites.iterrows():
        description = html.escape(
            f"Rank {int(row['rank'])}\n"
            f"Ponded channel length: {row['ponded_length_m']:.1f} m\n"
            f"Bed elevation: {row['bed_elevation_m']:.2f} m\n"
            f"Crest elevation: {row['crest_elevation_m']:.2f} m"
        ).replace("\n", "<br>")
        placemarks.append(f"""
        <Placemark>
          <name>Candidate {int(row['rank'])}: {row['ponded_length_m'] / 1000:.2f} km ponded channel</name>
          <styleUrl>#candidate</styleUrl>
          <description><![CDATA[{description}]]></description>
          <Point><coordinates>{row['lon']:.7f},{row['lat']:.7f},0</coordinates></Point>
        </Placemark>""")

    start, dam = int(selected.water_start_idx), int(selected.dam_idx)
    coordinate_text = " ".join(
        f"{lon:.7f},{lat:.7f},0"
        for lon, lat in profile.iloc[start : dam + 1][["lon", "lat"]].itertuples(index=False, name=None)
    )
    placemarks.append(f"""
    <Placemark>
      <name>Selected weir inundated centreline</name>
      <styleUrl>#water</styleUrl>
      <description><![CDATA[One-dimensional connected channel inundation only.]]></description>
      <LineString><tessellate>1</tessellate><coordinates>{coordinate_text}</coordinates></LineString>
    </Placemark>""")

    document = f"""<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Document>
        <name>Weir screening results</name>
        <Style id="candidate">
          <IconStyle><color>ffb5699b</color><scale>1.15</scale></IconStyle>
        </Style>
        <Style id="water">
          <LineStyle><color>ffc58217</color><width>6</width></LineStyle>
        </Style>
        {''.join(placemarks)}
      </Document>
    </kml>"""
    output_file.write_text(document, encoding="utf-8")


def print_summary(top_sites: pd.DataFrame, selected: pd.Series, reversed_input: bool, output_dir: Path) -> None:
    direction = "end → start" if reversed_input else "start → end"
    print("\n=== Weir siting screen complete ===")
    print(f"Input line was interpreted as flowing {direction}.")
    print(f"Selected scenario: {selected.ponded_length_m / 1000:.2f} km ponded channel")
    print(f"Selected position: {selected.lat:.6f}, {selected.lon:.6f}")
    print(f"Outputs: {output_dir.resolve()}")
    print("\nTop separated candidates:")
    display_cols = ["rank", "ponded_length_m", "dam_chainage_m", "lat", "lon", "mean_centreline_depth_m"]
    print(top_sites[display_cols].to_string(index=False, float_format=lambda value: f"{value:.2f}"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rank KML river-centreline weir sites by one-dimensional ponded channel length."
    )
    parser.add_argument("kml", type=Path, help="KML containing the river LineString.")
    parser.add_argument("--placemark-name", help="Exact KML Placemark name to analyse.")
    parser.add_argument("--weir-height-m", type=float, default=1.5, help="Height above local channel bed (default: 1.5).")
    parser.add_argument("--sample-spacing-m", type=float, default=30.0, help="Profile sample spacing (default: 30 m).")
    parser.add_argument("--smoothing-window-m", type=float, default=90.0, help="Median smoothing window for DEM noise (default: 90 m).")
    parser.add_argument("--dem", type=Path, help="Local bare-earth GeoTIFF. Preferred over the online DEM.")
    parser.add_argument(
        "--dem-dataset",
        default="srtm30m",
        choices=["srtm30m", "aster30m", "mapzen"],
        help="OpenTopoData dataset when --dem is not supplied (default: srtm30m).",
    )
    parser.add_argument(
        "--flow-direction",
        default="auto",
        choices=["auto", "start-to-end", "end-to-start"],
        help="Direction in which water flows in the input KML. Auto uses endpoint elevations.",
    )
    parser.add_argument("--min-ponded-length-m", type=float, default=75.0, help="Exclude negligible ponds (default: 75 m).")
    parser.add_argument("--edge-exclusion-m", type=float, default=60.0, help="Avoid river-line endpoints (default: 60 m).")
    parser.add_argument("--candidate-separation-m", type=float, default=250.0, help="Minimum separation between reported sites (default: 250 m).")
    parser.add_argument("--top-n", type=int, default=12, help="Number of separated candidate sites to publish (default: 12).")
    scenario = parser.add_mutually_exclusive_group()
    scenario.add_argument("--weir-chainage-m", type=float, help="Model a specified upstream-to-downstream chainage instead of the best site.")
    scenario.add_argument(
        "--weir-lonlat",
        type=float,
        nargs=2,
        metavar=("LON", "LAT"),
        help="Model the nearest river point to a specified longitude and latitude.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("weir_results"), help="Output directory.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.weir_height_m <= 0:
        raise ValueError("--weir-height-m must be positive.")
    if args.sample_spacing_m <= 0:
        raise ValueError("--sample-spacing-m must be positive.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    vertices = parse_kml_linestring(args.kml, args.placemark_name)
    profile = densify_line(vertices, args.sample_spacing_m)

    # KML altitude values are frequently ground-clamped or zero. A DEM is therefore used
    # unless a local GeoTIFF is supplied.
    if args.dem:
        print(f"Sampling local DEM: {args.dem}")
        profile["elevation_m"] = sample_local_dem(args.dem, profile["lon"], profile["lat"])
        dem_label = f"local DEM: {args.dem.name}"
    else:
        print(f"Sampling OpenTopoData {args.dem_dataset} DEM (this may take a few seconds)...")
        profile["elevation_m"] = sample_opentopodata(profile["lon"], profile["lat"], args.dem_dataset)
        dem_label = f"OpenTopoData {args.dem_dataset}"

    if profile["elevation_m"].isna().any():
        n_bad = int(profile["elevation_m"].isna().sum())
        raise RuntimeError(f"{n_bad} DEM sample(s) were missing. Use a local DEM covering the whole river line.")

    profile["elevation_smooth_m"] = smooth_profile(
        profile["elevation_m"].to_numpy(), args.sample_spacing_m, args.smoothing_window_m
    )
    profile, reversed_input = orient_upstream_to_downstream(profile, args.flow_direction)
    profile["dem_source"] = dem_label
    profile.to_csv(args.output_dir / "river_profile.csv", index=False)

    all_candidates = find_candidates(
        profile,
        args.weir_height_m,
        args.min_ponded_length_m,
        args.edge_exclusion_m,
    )
    if all_candidates.empty:
        raise RuntimeError(
            "No candidate meets --min-ponded-length-m. Lower that threshold or inspect the profile."
        )
    all_candidates.to_csv(args.output_dir / "all_candidate_positions.csv", index=False)

    top_sites = choose_spaced_sites(all_candidates, args.candidate_separation_m, args.top_n)
    top_sites.to_csv(args.output_dir / "top_weir_candidates.csv", index=False)

    if args.weir_chainage_m is not None:
        selected_df = candidate_for_requested_position(
            profile, args.weir_height_m, args.weir_chainage_m, None
        )
    elif args.weir_lonlat is not None:
        selected_df = candidate_for_requested_position(
            profile, args.weir_height_m, None, tuple(args.weir_lonlat)
        )
    else:
        selected_df = top_sites.iloc[[0]].copy()
    selected = selected_df.iloc[0]

    save_profile_plot(profile, selected, args.output_dir / "selected_weir_profile.png", args.weir_height_m)
    save_candidates_plot(profile, top_sites, args.output_dir / "top_weir_candidates.png")
    save_interactive_map(profile, top_sites, selected, args.output_dir / "weir_candidates_map.html")
    write_results_kml(profile, top_sites, selected, args.output_dir / "weir_screening_results.kml")
    print_summary(top_sites, selected, reversed_input, args.output_dir)


if __name__ == "__main__":
    main()


requirements = dedent('''
numpy>=1.26
pandas>=2.2
matplotlib>=3.8
pyproj>=3.6
folium>=0.17
requests>=2.31
# Optional: needed only when using --dem local_dem.tif
rasterio>=1.3
''').lstrip()

# readme = dedent('''
# # Weir siting screen

# This folder contains `weir_siting.py`, a one-dimensional channel-screening tool for
# the `Rietfontein Rhoodekranz River` KML line.

# ## Install

# ```bash
# python -m venv .venv
# # Windows: .venv\\Scripts\\activate
# # macOS/Linux:
# source .venv/bin/activate
# pip install -r requirements.txt