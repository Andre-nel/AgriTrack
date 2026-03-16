(function () {
  const mapElement = document.getElementById("farm-map");
  if (!mapElement || typeof L === "undefined") {
    return;
  }

  const statusElement = document.getElementById("farm-map-status");
  const dataUrl = mapElement.dataset.url;
  const baseLayerMode = String(mapElement.dataset.baseLayer || "street")
    .trim()
    .toLowerCase();
  const paddockFillMode = String(mapElement.dataset.paddockFill || "pressure")
    .trim()
    .toLowerCase();
  const showStockFloats = mapElement.dataset.showStockFloats !== "0";
  const hasWaterAssetFilter = mapElement.dataset.waterAssetFilter !== undefined;
  const selectedWaterAssetTypes = hasWaterAssetFilter
    ? new Set(
        String(mapElement.dataset.waterAssetFilter || "")
          .split(",")
          .map((value) => String(value || "").trim().toLowerCase())
          .filter(Boolean)
      )
    : null;
  const speciesIconUrls = {
    cattle: mapElement.dataset.cowIcon || "/static/cow.png",
    sheep: mapElement.dataset.sheepIcon || "/static/sheep.png",
    goat: mapElement.dataset.goatIcon || "/static/goat.png",
  };
  const pressureColors = [
    "#274e13",
    "#38761d",
    "#6aa84f",
    "#93c47d",
    "#b6d7a8",
    "#ffe599",
    "#f6b26b",
    "#b45f06",
    "#e06666",
    "#cc0000",
  ];
  const waterAssetColors = {
    borehole: "#166f9a",
    pit: "#8d6442",
    windmill: "#5e7183",
    solarpump: "#cc7b22",
    cement_dam: "#2b6d7f",
    tank: "#2f8f88",
    ground_dam: "#7b5c37",
    weir: "#4f73a8",
    trough: "#1f5fb8",
  };
  const waterAssetCodes = {
    borehole: "BH",
    pit: "PT",
    windmill: "WM",
    solarpump: "SP",
    cement_dam: "CD",
    tank: "TK",
    ground_dam: "GD",
    weir: "WR",
    trough: "TR",
  };
  if (!dataUrl) {
    if (statusElement) {
      statusElement.textContent = "Map data URL is missing.";
    }
    return;
  }

  function setStatus(message) {
    if (statusElement) {
      statusElement.textContent = message;
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatNumber(value, precision) {
    if (value === null || value === undefined || Number.isNaN(value)) {
      return "-";
    }
    return Number(value).toFixed(precision);
  }

  function colorForPressure(ratio) {
    if (ratio === null || ratio === undefined) {
      return "#8f9a96";
    }
    const value = Number(ratio);
    if (Number.isNaN(value)) {
      return "#8f9a96";
    }
    const clamped = Math.max(0, Math.min(1, value));
    const idx = Math.min(pressureColors.length - 1, Math.floor(clamped * pressureColors.length));
    return pressureColors[idx];
  }

  function normalizeSpecies(value) {
    return String(value || "").trim().toLowerCase();
  }

  function formatHeadCount(value) {
    if (value === null || value === undefined || Number.isNaN(value)) {
      return "0";
    }
    const rounded = Math.round(Number(value));
    if (Math.abs(Number(value) - rounded) < 0.05) {
      return String(rounded);
    }
    return Number(value).toFixed(1);
  }

  function stockFloatHtml(properties) {
    const speciesRows = Array.isArray(properties.species_heads) ? properties.species_heads : [];
    const rows = speciesRows
      .map((row) => ({
        species: normalizeSpecies(row.species),
        count: Number(row.head || 0),
      }))
      .filter((row) => row.count > 0)
      .sort((a, b) => b.count - a.count);

    if (!rows.length) {
      return "";
    }

    const chips = rows
      .map((row) => {
        const iconUrl = speciesIconUrls[row.species] || "";
        const speciesLabel = row.species ? row.species.charAt(0).toUpperCase() + row.species.slice(1) : "Stock";
        return (
          '<div class="stock-float-chip" title="' +
          escapeHtml(speciesLabel + ": " + formatHeadCount(row.count)) +
          '">' +
          (iconUrl
            ? '<img src="' + escapeHtml(iconUrl) + '" alt="' + escapeHtml(speciesLabel) + '" />'
            : '<span class="stock-float-fallback">' + escapeHtml(speciesLabel.slice(0, 1)) + "</span>") +
          '<span class="stock-float-count">' +
          escapeHtml(formatHeadCount(row.count)) +
          "</span>" +
          "</div>"
        );
      })
      .join("");

    return (
      '<div class="stock-float-badge" aria-hidden="true">' +
      '<div class="stock-float-chip-row">' +
      chips +
      "</div>" +
      '<div class="stock-float-tail"></div>' +
      "</div>"
    );
  }

  function addStockFloatMarker(feature, geoLayer, markerLayer) {
    if (!markerLayer) {
      return;
    }
    const props = (feature && feature.properties) || {};
    if (props.feature_type !== "paddock") {
      return;
    }

    const html = stockFloatHtml(props);
    if (!html) {
      return;
    }

    const bounds = geoLayer.getBounds();
    if (!bounds.isValid()) {
      return;
    }

    const marker = L.marker(bounds.getCenter(), {
      pane: "stockFloatPane",
      interactive: false,
      keyboard: false,
      icon: L.divIcon({
        className: "stock-float-marker-wrap",
        html: '<div class="stock-float-marker">' + html + "</div>",
        iconSize: [1, 1],
        iconAnchor: [0, 0],
      }),
    });
    markerLayer.addLayer(marker);
  }

  function addPaddockNameLabel(feature, geoLayer, labelLayer) {
    const props = (feature && feature.properties) || {};
    if (props.feature_type !== "paddock") {
      return;
    }

    const name = String(props.name || "").trim();
    if (!name) {
      return;
    }

    const bounds = geoLayer.getBounds();
    if (!bounds.isValid()) {
      return;
    }

    const hasStock = Number(props.current_lsu || 0) > 0.01;
    const labelClass = hasStock ? "map-paddock-label map-paddock-label-bold" : "map-paddock-label";
    const marker = L.marker(bounds.getCenter(), {
      pane: "paddockLabelPane",
      interactive: false,
      keyboard: false,
      icon: L.divIcon({
        className: "map-paddock-label-wrap",
        html: '<span class="' + labelClass + '">' + escapeHtml(name) + "</span>",
        iconSize: [1, 1],
        iconAnchor: [0, 0],
      }),
    });
    labelLayer.addLayer(marker);
  }

  function waterAssetMarker(feature, latlng) {
    const props = (feature && feature.properties) || {};
    const assetType = String(props.asset_type || "").trim().toLowerCase();
    const color = waterAssetColors[assetType] || "#2f7a5c";
    const code = waterAssetCodes[assetType] || "W";
    return L.marker(latlng, {
      icon: L.divIcon({
        className: "water-asset-marker-wrap",
        html:
          '<span class="water-asset-marker" style="--water-asset-color:' +
          escapeHtml(color) +
          '">' +
          escapeHtml(code) +
          "</span>",
        iconSize: [28, 28],
        iconAnchor: [14, 14],
      }),
    });
  }

  function normalizeAssetType(value) {
    return String(value || "").trim().toLowerCase();
  }

  function isWaterAssetVisible(properties) {
    if (!selectedWaterAssetTypes) {
      return true;
    }
    return selectedWaterAssetTypes.has(normalizeAssetType(properties.asset_type));
  }

  function isWaterConnectionVisible(properties) {
    if (!selectedWaterAssetTypes) {
      return true;
    }
    const sourceVisible = selectedWaterAssetTypes.has(normalizeAssetType(properties.source_asset_type));
    const destinationVisible = selectedWaterAssetTypes.has(normalizeAssetType(properties.destination_asset_type));
    const pumpType = normalizeAssetType(properties.pump_asset_type);
    return sourceVisible && destinationVisible && (!pumpType || selectedWaterAssetTypes.has(pumpType));
  }

  function isFeatureVisible(feature) {
    const props = (feature && feature.properties) || {};
    if (props.feature_type === "water_asset") {
      return isWaterAssetVisible(props);
    }
    if (props.feature_type === "water_connection") {
      return isWaterConnectionVisible(props);
    }
    return true;
  }

  function styleForFeature(feature) {
    const props = feature.properties || {};
    if (props.feature_type === "farm_boundary") {
      return {
        color: "#2b5d4f",
        weight: 2.4,
        fill: false,
        opacity: 0.95,
      };
    }

    if (props.feature_type === "water_connection") {
      const gravity = props.flow_type === "gravity";
      return {
        color: gravity ? "#2c7fb8" : "#d17f2a",
        weight: gravity ? 2.8 : 3.2,
        opacity: 0.96,
        dashArray: gravity ? "8 6" : null,
      };
    }

    if (props.feature_type === "paddock") {
      if (paddockFillMode === "outline") {
        return {
          color: "#f6efb6",
          weight: 2.1,
          fillColor: "#ffffff",
          fillOpacity: 0.04,
          opacity: 0.92,
        };
      }
      return {
        color: "#44514d",
        weight: 1.2,
        fillColor: colorForPressure(props.grazing_pressure_ratio),
        fillOpacity: 0.62,
        opacity: 0.95,
      };
    }

    return {
      color: "#596560",
      weight: 1.2,
      fillColor: "#8f9a96",
      fillOpacity: 0.25,
      opacity: 0.9,
      dashArray: "4 4",
    };
  }

  function popupHtml(properties) {
    const props = properties || {};
    const name = escapeHtml(props.name || "Unnamed");
    const farmText = props.farm_name ? escapeHtml(props.farm_name) : "";
    const farmLine = farmText ? '<div class="map-popup-farm">' + farmText + "</div>" : "";

    if (props.feature_type !== "paddock") {
      if (props.feature_type === "water_asset") {
        const servedPaddocks = Array.isArray(props.served_paddocks) ? props.served_paddocks : [];
        const servedText = servedPaddocks.length
          ? servedPaddocks.map((row) => escapeHtml(row.name)).join("<br>")
          : "None";
        const locationText = props.location_paddock_name ? escapeHtml(props.location_paddock_name) : "None";
        const reviewText = props.needs_review ? "Yes" : "No";
        const assetTarget = props.id ? "asset-" + String(props.id) : "";
        const openAssetLink =
          props.asset_workspace_url && assetTarget
            ? '<a href="' +
              escapeHtml(props.asset_workspace_url + "#" + assetTarget) +
              '" data-water-asset-target="' +
              escapeHtml(assetTarget) +
              '">Open asset</a>'
            : "";
        return [
          farmLine,
          "<strong>" + name + "</strong>",
          '<div class="map-popup-farm">' + escapeHtml(props.asset_type_label || "Water Asset") + "</div>",
          '<table class="map-popup-table">',
          "<tr><td>Location Paddock</td><td>" + locationText + "</td></tr>",
          "<tr><td>Status</td><td>" + escapeHtml(props.status || "-") + "</td></tr>",
          "<tr><td>Water Level</td><td>" + escapeHtml(props.water_level || "-") + "</td></tr>",
          "<tr><td>Capacity (m3)</td><td>" + formatNumber(props.capacity_m3, 2) + "</td></tr>",
          "<tr><td>Needs Review</td><td>" + reviewText + "</td></tr>",
          "</table>",
          "<strong>Served Paddocks</strong><br>" + servedText,
          openAssetLink ? "<br>" + openAssetLink : "",
        ].join("");
      }

      if (props.feature_type === "water_connection") {
        const pumpText = props.pump_asset_name ? escapeHtml(props.pump_asset_name) : "-";
        const pipeBits = [
          props.pipe_material,
          props.pipe_diameter_spec,
          props.pipe_wall_thickness_spec,
          props.pipe_class_spec,
          props.pipe_quality_spec,
        ].filter(Boolean);
        return [
          farmLine,
          "<strong>" +
            escapeHtml(props.source_asset_name || "Unknown") +
            " -> " +
            escapeHtml(props.destination_asset_name || "Unknown") +
            "</strong>",
          '<div class="map-popup-farm">' + escapeHtml(props.flow_type_label || "Water Connection") + "</div>",
          '<table class="map-popup-table">',
          "<tr><td>Flow Type</td><td>" + escapeHtml(props.flow_type_label || props.flow_type || "-") + "</td></tr>",
          "<tr><td>Pump Asset</td><td>" + pumpText + "</td></tr>",
          "<tr><td>Pipe Spec</td><td>" + escapeHtml(pipeBits.join(" | ") || "-") + "</td></tr>",
          "</table>",
          props.notes ? "<strong>Notes</strong><br>" + escapeHtml(props.notes) : "",
        ].join("");
      }

      const label = props.feature_type === "farm_boundary" ? "Farm Boundary" : "Unmatched Shape";
      return farmLine + "<strong>" + name + "</strong><br>" + label;
    }

    const speciesRows = Array.isArray(props.species_heads) ? props.species_heads : [];
    const speciesText = speciesRows.length
      ? speciesRows
          .map((row) => escapeHtml(row.species) + ": " + formatNumber(row.head, 1))
          .join("<br>")
      : "No active stock";

    const mobRows = Array.isArray(props.mobs) ? props.mobs : [];
    const mobText = mobRows.length
      ? mobRows
          .map(
            (row) =>
              escapeHtml(row.mob_name) +
              " (" +
              formatNumber(row.allocation_pct, 1) +
              "%, " +
              formatNumber(row.allocated_lsu, 2) +
              " LSU)"
          )
          .join("<br>")
      : "No active mobs";

    const detailLink = props.paddock_url
      ? '<a href="' + escapeHtml(props.paddock_url) + '">Open paddock</a>'
      : "";
    const pressureText =
      props.grazing_pressure_ratio === null || props.grazing_pressure_ratio === undefined
        ? "-"
        : formatNumber(props.grazing_pressure_ratio * 100, 1) + "%";
    const currentActivityLabel = escapeHtml(props.current_activity_label || "Current Activity");

    return [
      farmLine,
      "<strong>" + name + "</strong>",
      '<table class="map-popup-table">',
      "<tr><td>" + currentActivityLabel + "</td><td>" + formatNumber(props.current_activity_days, 1) + "</td></tr>",
      "<tr><td>Paddock Area (ha)</td><td>" + formatNumber(props.area_ha, 2) + "</td></tr>",
      "<tr><td>Current LSU</td><td>" + formatNumber(props.current_lsu, 2) + "</td></tr>",
      "<tr><td>Grazing Intensity (Hectares/LSU)</td><td>" + formatNumber(props.paddock_ha_per_current_lsu, 2) + "</td></tr>",
      "<tr><td>Used SDH</td><td>" + formatNumber(props.sdh_used_this_year, 3) + "</td></tr>",
      "<tr><td>Capacity SDH</td><td>" + formatNumber(props.grazing_capacity_sdh, 3) + "</td></tr>",
      "<tr><td>Pressure</td><td>" + pressureText + "</td></tr>",
      "</table>",
      "<strong>Species / Head</strong><br>" + speciesText,
      "<br><strong>Active Mobs</strong><br>" + mobText,
      detailLink ? "<br>" + detailLink : "",
    ].join("");
  }

  function getFullscreenElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }

  function requestMapFullscreen() {
    if (typeof mapElement.requestFullscreen === "function") {
      return mapElement.requestFullscreen();
    }
    if (typeof mapElement.webkitRequestFullscreen === "function") {
      return mapElement.webkitRequestFullscreen();
    }
    return Promise.reject(new Error("Full-screen mode is not supported in this browser."));
  }

  function exitFullscreen() {
    if (typeof document.exitFullscreen === "function") {
      return document.exitFullscreen();
    }
    if (typeof document.webkitExitFullscreen === "function") {
      return document.webkitExitFullscreen();
    }
    return Promise.reject(new Error("Full-screen mode is not supported in this browser."));
  }

  function openWaterAssetDetails(targetId, options) {
    const details = targetId ? document.getElementById(targetId) : null;
    if (!details || details.tagName !== "DETAILS") {
      return false;
    }

    details.open = true;
    if (options && options.updateHash && window.location.hash !== "#" + targetId) {
      window.history.replaceState(null, "", "#" + targetId);
    }

    window.setTimeout(function () {
      details.scrollIntoView({
        behavior: options && options.smooth ? "smooth" : "auto",
        block: "start",
      });
      const summary = details.querySelector("summary");
      if (summary && typeof summary.focus === "function") {
        summary.focus({ preventScroll: true });
      }
    }, 0);
    return true;
  }

  function openWaterAssetFromHash() {
    const hash = String(window.location.hash || "").replace(/^#/, "");
    if (!hash || hash.indexOf("asset-") !== 0) {
      return;
    }
    openWaterAssetDetails(hash, { smooth: false });
  }

  const map = L.map(mapElement, { scrollWheelZoom: true });
  map.createPane("paddockLabelPane");
  map.getPane("paddockLabelPane").style.zIndex = "640";
  map.getPane("paddockLabelPane").style.pointerEvents = "none";
  map.createPane("stockFloatPane");
  map.getPane("stockFloatPane").style.zIndex = "650";
  map.getPane("stockFloatPane").style.pointerEvents = "none";

  const baseLayer =
    baseLayerMode === "satellite"
      ? L.tileLayer(
          "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
          {
            maxZoom: 20,
            attribution:
              "Powered by Esri | Sources: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
          }
        )
      : L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 20,
          attribution: "&copy; OpenStreetMap contributors",
        });
  baseLayer.addTo(map);

  map.setView([-32.9102, 25.449], 13);

  let fullscreenButton = null;
  let labelToggleButton = null;
  let paddockLabelLayer = null;
  let labelsVisible = true;

  function isMapFullscreen() {
    return getFullscreenElement() === mapElement;
  }

  function refreshMapSize() {
    window.requestAnimationFrame(() => map.invalidateSize(false));
    window.setTimeout(() => map.invalidateSize(false), 180);
  }

  function updateFullscreenButton() {
    if (!fullscreenButton) {
      return;
    }
    const isFullscreen = isMapFullscreen();
    fullscreenButton.textContent = isFullscreen ? "Exit" : "Expand";
    fullscreenButton.setAttribute("aria-label", isFullscreen ? "Exit full screen map" : "Open full screen map");
    fullscreenButton.setAttribute("aria-pressed", isFullscreen ? "true" : "false");
    fullscreenButton.title = isFullscreen ? "Exit full screen" : "Open full screen";
  }

  function syncPaddockLabelVisibility() {
    if (!paddockLabelLayer) {
      return;
    }
    if (labelsVisible) {
      if (!map.hasLayer(paddockLabelLayer)) {
        paddockLabelLayer.addTo(map);
      }
      return;
    }
    if (map.hasLayer(paddockLabelLayer)) {
      map.removeLayer(paddockLabelLayer);
    }
  }

  function updateLabelToggleButton() {
    if (!labelToggleButton) {
      return;
    }
    labelToggleButton.textContent = labelsVisible ? "Names Off" : "Names On";
    labelToggleButton.setAttribute("aria-pressed", labelsVisible ? "true" : "false");
    labelToggleButton.setAttribute(
      "aria-label",
      labelsVisible ? "Hide paddock names on the map" : "Show paddock names on the map"
    );
    labelToggleButton.title = labelsVisible ? "Hide paddock names" : "Show paddock names";
  }

  function togglePaddockLabels() {
    labelsVisible = !labelsVisible;
    syncPaddockLabelVisibility();
    updateLabelToggleButton();
  }

  const LabelToggleControl = L.Control.extend({
    options: {
      position: "topright",
    },
    onAdd: function () {
      const container = L.DomUtil.create("div", "leaflet-bar map-label-toggle-control");
      labelToggleButton = L.DomUtil.create("button", "map-control-button map-label-toggle", container);
      labelToggleButton.type = "button";
      updateLabelToggleButton();
      L.DomEvent.disableClickPropagation(container);
      L.DomEvent.on(labelToggleButton, "click", function (event) {
        L.DomEvent.stop(event);
        togglePaddockLabels();
      });
      return container;
    },
  });

  const FullscreenControl = L.Control.extend({
    options: {
      position: "topright",
    },
    onAdd: function () {
      const container = L.DomUtil.create("div", "leaflet-bar map-fullscreen-control");
      fullscreenButton = L.DomUtil.create("button", "map-control-button map-fullscreen-toggle", container);
      fullscreenButton.type = "button";
      updateFullscreenButton();
      L.DomEvent.disableClickPropagation(container);
      L.DomEvent.on(fullscreenButton, "click", function (event) {
        L.DomEvent.stop(event);
        const action = isMapFullscreen() ? exitFullscreen() : requestMapFullscreen();
        Promise.resolve(action).catch((error) => {
          setStatus(error.message);
        });
      });
      return container;
    },
  });

  map.addControl(new LabelToggleControl());
  map.addControl(new FullscreenControl());

  document.addEventListener("fullscreenchange", function () {
    updateFullscreenButton();
    refreshMapSize();
  });
  document.addEventListener("webkitfullscreenchange", function () {
    updateFullscreenButton();
    refreshMapSize();
  });
  document.addEventListener("click", function (event) {
    const link = event.target.closest("[data-water-asset-target]");
    if (!link) {
      return;
    }
    const targetId = String(link.dataset.waterAssetTarget || "").trim();
    if (openWaterAssetDetails(targetId, { smooth: true, updateHash: true })) {
      event.preventDefault();
    }
  });
  window.addEventListener("hashchange", openWaterAssetFromHash);
  openWaterAssetFromHash();

  fetch(dataUrl, { headers: { Accept: "application/json" } })
    .then((response) => {
      if (!response.ok) {
        throw new Error("Map data is unavailable. Check instance/maps/<farm name>.kml.");
      }
      return response.json();
    })
    .then((payload) => {
      const allFeatures = Array.isArray(payload.features) ? payload.features : [];
      const features = allFeatures.filter(isFeatureVisible);
      const warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
      const missingKmlFarms = Array.isArray(payload.missing_kml_farms)
        ? payload.missing_kml_farms.length
        : 0;
      const invalidKmlFarms = Array.isArray(payload.invalid_kml_farms)
        ? payload.invalid_kml_farms.length
        : 0;
      const visibleWaterFeatureCount = features.filter((feature) => {
        const props = (feature && feature.properties) || {};
        return props.feature_type === "water_asset" || props.feature_type === "water_connection";
      }).length;
      const allWaterFeatureCount = allFeatures.filter((feature) => {
        const props = (feature && feature.properties) || {};
        return props.feature_type === "water_asset" || props.feature_type === "water_connection";
      }).length;
      if (!features.length) {
        if (warnings.length) {
          setStatus(warnings.join(" "));
        } else if (missingKmlFarms > 0 || invalidKmlFarms > 0) {
          setStatus(
            "No map polygons could be loaded. Missing farm KML files: " +
              missingKmlFarms +
              ". Invalid farm KML files: " +
              invalidKmlFarms +
              "."
          );
        } else {
          setStatus("No polygons were found in the KML.");
        }
        return;
      }

      paddockLabelLayer = L.layerGroup().addTo(map);
      const stockFloatLayer = showStockFloats ? L.layerGroup().addTo(map) : null;
      const layer = L.geoJSON(payload, {
        style: styleForFeature,
        pointToLayer: function (feature, latlng) {
          const props = (feature && feature.properties) || {};
          if (props.feature_type === "water_asset") {
            return waterAssetMarker(feature, latlng);
          }
          return L.marker(latlng);
        },
        onEachFeature: function (feature, geoLayer) {
          geoLayer.bindPopup(popupHtml(feature.properties || {}), { maxWidth: 360 });
          addPaddockNameLabel(feature, geoLayer, paddockLabelLayer);
          addStockFloatMarker(feature, geoLayer, stockFloatLayer);
        },
        filter: isFeatureVisible,
      }).addTo(map);
      syncPaddockLabelVisibility();
      updateLabelToggleButton();

      const bounds = layer.getBounds();
      if (bounds.isValid()) {
        map.fitBounds(bounds.pad(0.08));
      }

      const unmatched = Array.isArray(payload.unmatched_placemarks)
        ? payload.unmatched_placemarks.length
        : 0;
      const missing = Array.isArray(payload.paddocks_without_kml)
        ? payload.paddocks_without_kml.length
        : 0;

      if (warnings.length || unmatched > 0 || missing > 0 || missingKmlFarms > 0 || invalidKmlFarms > 0) {
        const warningParts = [];
        if (warnings.length) {
          warningParts.push(warnings.join(" "));
        }
        if (missingKmlFarms > 0) {
          warningParts.push("Missing farm KML files: " + missingKmlFarms + ".");
        }
        if (invalidKmlFarms > 0) {
          warningParts.push("Invalid farm KML files: " + invalidKmlFarms + ".");
        }
        if (unmatched > 0) {
          warningParts.push("Unmatched KML shapes: " + unmatched + ".");
        }
        if (missing > 0) {
          warningParts.push("Paddocks without map geometry: " + missing + ".");
        }
        if (hasWaterAssetFilter && allWaterFeatureCount > 0 && visibleWaterFeatureCount === 0) {
          warningParts.push("Current water asset filters hide all water features.");
        }
        setStatus("Map loaded with warnings. " + warningParts.join(" "));
      } else {
        if (hasWaterAssetFilter && allWaterFeatureCount > 0 && visibleWaterFeatureCount === 0) {
          setStatus("Map loaded. Current water asset filters hide all water features.");
        } else {
          setStatus("Map loaded.");
        }
      }
    })
    .catch((error) => {
      setStatus(error.message);
    });
})();
