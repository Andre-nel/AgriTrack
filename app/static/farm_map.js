(function () {
  const mapElement = document.getElementById("farm-map");
  if (!mapElement || typeof L === "undefined") {
    return;
  }

  const statusElement = document.getElementById("farm-map-status");
  const dataUrl = mapElement.dataset.url;
  const mapFocus = String(mapElement.dataset.mapFocus || "").trim().toLowerCase();
  const fenceFocusMode = mapFocus === "fences";
  const baseLayerMode = String(mapElement.dataset.baseLayer || "street")
    .trim()
    .toLowerCase();
  const paddockFillMode = String(mapElement.dataset.paddockFill || "pressure")
    .trim()
    .toLowerCase();
  const showStockFloats = mapElement.dataset.showStockFloats !== "0";
  const gateCreateUrl = mapElement.dataset.gateCreateUrl || "";
  const gateRequiresFarmSelection = mapElement.dataset.gateRequiresFarmSelection === "1";
  const configuredGateFarmOptions = parseGateFarmOptions(mapElement.dataset.gateFarmOptions || "[]");
  const configuredGatePaddockOptions = parseGatePaddockOptions(mapElement.dataset.gatePaddockOptions || "[]");
  const gateDetailUrlTemplate = mapElement.dataset.gateDetailUrlTemplate || "";
  const gateStateUrlTemplate = mapElement.dataset.gateStateUrlTemplate || "";
  const gateLocationUrlTemplate = mapElement.dataset.gateLocationUrlTemplate || "";
  const fenceCreateUrl = mapElement.dataset.fenceCreateUrl || "";
  const fenceUpdateUrlTemplate = mapElement.dataset.fenceUpdateUrlTemplate || "";
  const fenceDeleteUrlTemplate = mapElement.dataset.fenceDeleteUrlTemplate || "";
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
  const gateIconUrls = {
    open: mapElement.dataset.gateOpenIcon || "/static/ranch_gate_open.svg",
    closed: mapElement.dataset.gateClosedIcon || "/static/ranch_gate_closed.svg",
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
  const storageWaterAssetTypes = new Set(["cement_dam", "tank", "ground_dam", "weir", "trough"]);
  const levelOutlineWaterAssetTypes = new Set(["cement_dam", "tank", "trough"]);
  const waterLevelVisuals = {
    full: { ratio: 1, outline: "#2563eb" },
    high: { ratio: 0.75, outline: "#3b82f6" },
    half: { ratio: 0.5, outline: "#60a5fa" },
    low: { ratio: 0.25, outline: "#fca5a5" },
    empty: { ratio: 0, outline: "#ef4444" },
  };
  const waterAssetStatusColors = {
    operational: "#16a34a",
    limited: "#f59e0b",
    dry: "#ef4444",
    service_due: "#f59e0b",
    down: "#ef4444",
    leaking: "#f59e0b",
    silted: "#f59e0b",
    damaged: "#ef4444",
  };
  const neutralWaterAssetColor = "#64748b";
  const waterAssetMarkerPath =
    "M16 2C9.1 2 3.5 7.6 3.5 14.5c0 8.7 7.6 14.6 11.2 22.1.5 1 1.6 1 2.1 0C20.4 29.1 28 23.2 28 14.5 28 7.6 22.4 2 16 2Z";
  const waterDropPath = "M16 9c-3.4 4-5 6.7-5 9.1a5 5 0 0 0 10 0c0-2.4-1.6-5.1-5-9.1Z";
  const waterAssetFontFamily = "'Segoe UI', 'Trebuchet MS', sans-serif";
  let waterAssetMarkerSequence = 0;
  const gateMarkersById = new Map();
  const fenceLayersById = new Map();
  const fenceRegisterRowsById = new Map();
  const fencePanel = document.querySelector("[data-fence-map-panel]");
  const fenceForm = document.querySelector("[data-fence-map-form]");
  const fencePanelTitle = document.querySelector("[data-fence-panel-title]");
  const fencePanelStatus = document.querySelector("[data-fence-panel-status]");
  const fenceNewButton = document.querySelector("[data-fence-map-new]");
  const fenceEditButton = document.querySelector("[data-fence-map-edit]");
  const fenceSaveButton = document.querySelector("[data-fence-map-save]");
  const fenceCancelButton = document.querySelector("[data-fence-map-cancel]");
  const fenceArchiveButton = document.querySelector("[data-fence-map-archive]");
  const fenceDetailLink = document.querySelector("[data-fence-map-detail-link]");
  const fenceRegisterBody = document.querySelector("[data-fence-register-body]");
  let selectedFenceId = "";
  let selectedFenceFeature = null;
  let selectedFenceLayer = null;
  let fenceEditGeometry = null;
  let fenceEditing = false;
  let fenceDrawing = false;
  let fenceDrawPoints = [];
  let fenceEditLineLayer = null;
  let fenceEditMarkerLayer = null;
  let fenceDrawLayer = null;
  let fenceDrawMarkerLayer = null;
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

  function gateLocationUrl(gateId, directUrl) {
    if (directUrl) {
      return directUrl;
    }
    if (!gateLocationUrlTemplate || !gateId) {
      return "";
    }
    return gateLocationUrlTemplate.replace("__gate_id__", encodeURIComponent(gateId));
  }

  function gateDetailUrl(gateId, directUrl) {
    if (directUrl) {
      return directUrl;
    }
    if (!gateDetailUrlTemplate || !gateId) {
      return "";
    }
    return gateDetailUrlTemplate.replace("__gate_id__", encodeURIComponent(gateId));
  }

  function gateStateUrl(gateId, directUrl) {
    if (directUrl) {
      return directUrl;
    }
    if (!gateStateUrlTemplate || !gateId) {
      return "";
    }
    return gateStateUrlTemplate.replace("__gate_id__", encodeURIComponent(gateId));
  }

  function fenceUpdateUrl(fenceId) {
    if (!fenceUpdateUrlTemplate || !fenceId) {
      return "";
    }
    return fenceUpdateUrlTemplate.replace("__fence_id__", encodeURIComponent(fenceId));
  }

  function fenceDeleteUrl(fenceId) {
    if (!fenceDeleteUrlTemplate || !fenceId) {
      return "";
    }
    return fenceDeleteUrlTemplate.replace("__fence_id__", encodeURIComponent(fenceId));
  }

  function requestJson(url, method, payload) {
    if (!url) {
      return Promise.reject(new Error("Fence map updates are not available."));
    }
    const options = {
      method: method,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
    };
    if (payload !== undefined) {
      options.body = JSON.stringify(payload);
    }
    return fetch(url, options).then((response) =>
      response.json().then((body) => {
        if (!response.ok) {
          throw new Error(body.error || "Fence could not be saved.");
        }
        return body;
      })
    );
  }

  function fetchGateDetail(gateId, directUrl) {
    const url = gateDetailUrl(gateId, directUrl);
    if (!url) {
      return Promise.reject(new Error("Gate details are not available."));
    }
    return fetch(url, { headers: { Accept: "application/json" } }).then((response) =>
      response.json().then((payload) => {
        if (!response.ok) {
          throw new Error(payload.error || "Gate details could not be loaded.");
        }
        return payload;
      })
    );
  }

  function postGateState(gateId, status, closureChoices, directUrl) {
    const url = gateStateUrl(gateId, directUrl);
    if (!url) {
      return Promise.reject(new Error("Gate state updates are not available."));
    }
    return fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        status: status,
        closure_choices: closureChoices || [],
      }),
    }).then((response) =>
      response.json().then((payload) => {
        if (!response.ok) {
          throw new Error(payload.error || "Gate state could not be saved.");
        }
        return payload;
      })
    );
  }

  function postGateLocation(gateId, latlng, directUrl) {
    const url = gateLocationUrl(gateId, directUrl);
    if (!url) {
      return Promise.reject(new Error("Gate location updates are not available."));
    }
    return fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        latitude: latlng.lat,
        longitude: latlng.lng,
      }),
    }).then((response) =>
      response.json().then((payload) => {
        if (!response.ok) {
          throw new Error(payload.error || "Gate location could not be saved.");
        }
        return payload;
      })
    );
  }

  function postGateCreate(payload) {
    if (!gateCreateUrl) {
      return Promise.reject(new Error("Gate creation is not available."));
    }
    return fetch(gateCreateUrl, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    }).then((response) =>
      response.json().then((body) => {
        if (!response.ok) {
          throw new Error(body.error || "Gate could not be created.");
        }
        return body;
      })
    );
  }

  function gateFeatureFromGate(gate) {
    const latitude = Number(gate && gate.latitude);
    const longitude = Number(gate && gate.longitude);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
      return null;
    }
    return {
      type: "Feature",
      geometry: {
        type: "Point",
        coordinates: [longitude, latitude],
      },
      properties: Object.assign({}, gate, {
        feature_type: "gate",
      }),
    };
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

  function normalizeKey(value) {
    return String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[\s-]+/g, "_");
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
    const assetType = normalizeAssetType(props.asset_type);
    const code = waterAssetCodes[assetType] || "W";
    const html = storageWaterAssetTypes.has(assetType)
      ? storageWaterAssetSvg(props, code)
      : statusWaterAssetSvg(props, code);
    return L.marker(latlng, {
      icon: L.divIcon({
        className: "water-asset-marker-wrap",
        html: '<span class="water-asset-marker">' + html + "</span>",
        iconSize: [32, 42],
        iconAnchor: [16, 37],
      }),
    });
  }

  function gateIcon(props) {
    const status = normalizeKey(props.status) === "open" ? "open" : "closed";
    const label = (props.name || "Gate") + " " + status;
    return L.divIcon({
      className: "gate-marker-wrap",
      html:
        '<span class="gate-marker gate-marker-' +
        status +
        '" title="' +
        escapeHtml(label) +
        '"><span class="gate-marker-post"></span><img class="gate-marker-img" src="' +
        escapeHtml(gateIconUrls[status]) +
        '" alt="' +
        escapeHtml(label) +
        '" /></span>',
      iconSize: [32, 24],
      iconAnchor: [16, 12],
    });
  }

  function gateMarker(feature, latlng) {
    const props = (feature && feature.properties) || {};
    const gateId = props.gate_id || props.id;
    const locationUrl = props.gate_location_url || "";
    const marker = L.marker(latlng, {
      pane: "gatePane",
      draggable: Boolean(gateId && gateLocationUrl(gateId, locationUrl)),
      autoPan: true,
      icon: gateIcon(props),
    });
    marker._agriGateProps = props;
    if (gateId && gateLocationUrl(gateId, locationUrl)) {
      let previousLatLng = latlng;
      marker.on("dragstart", function () {
        previousLatLng = marker.getLatLng();
        marker.closePopup();
        setStatus("Move the gate, then release to save its location.");
      });
      marker.on("dragend", function () {
        const nextLatLng = marker.getLatLng();
        setStatus("Saving gate location...");
        postGateLocation(gateId, nextLatLng, props.gate_location_url || "")
          .then((payload) => {
            const gate = (payload && payload.gate) || {};
            props.latitude = gate.latitude;
            props.longitude = gate.longitude;
            props.source = gate.source || props.source;
            if (feature && feature.geometry && Array.isArray(feature.geometry.coordinates)) {
              feature.geometry.coordinates = [nextLatLng.lng, nextLatLng.lat];
            }
            marker.bindPopup(popupHtml(props), { maxWidth: 360 });
            setStatus("Gate location saved.");
          })
          .catch((error) => {
            marker.setLatLng(previousLatLng);
            setStatus(error.message || "Gate location could not be saved.");
          });
      });
    }
    if (gateId) {
      gateMarkersById.set(String(gateId), marker);
    }
    return marker;
  }

  function addOrUpdateGateFeature(gate, featureLayer) {
    const feature = gateFeatureFromGate(gate);
    if (!feature) {
      return null;
    }
    const gateId = String(feature.properties.gate_id || feature.properties.id || "");
    const latlng = L.latLng(feature.geometry.coordinates[1], feature.geometry.coordinates[0]);
    const existingMarker = gateMarkersById.get(gateId);
    if (existingMarker) {
      const existingProps = existingMarker._agriGateProps || {};
      Object.assign(existingProps, feature.properties);
      existingMarker._agriGateProps = existingProps;
      existingMarker.setLatLng(latlng);
      existingMarker.setIcon(gateIcon(existingProps));
      existingMarker.bindPopup(popupHtml(existingProps), { maxWidth: 360 });
      updateGateToggleButton();
      return existingMarker;
    }
    if (featureLayer) {
      featureLayer.addData(feature);
      updateGateToggleButton();
      return gateMarkersById.get(gateId) || null;
    }
    return null;
  }

  function normalizeAssetType(value) {
    return normalizeKey(value);
  }

  function waterAssetStatusColor(status) {
    return waterAssetStatusColors[normalizeKey(status)] || neutralWaterAssetColor;
  }

  function fenceConditionColor(condition) {
    const colors = {
      unknown: "#64748b",
      good: "#16a34a",
      fair: "#ca8a04",
      bad: "#ea580c",
      critical: "#dc2626",
    };
    return colors[normalizeKey(condition)] || colors.unknown;
  }

  function fenceConstructionLineStyle(constructionType) {
    const styles = {
      mesh: { dashArray: null, lineCap: "round" },
      high_strung_wire: { dashArray: "12 8", lineCap: "butt" },
      barbed_wire: { dashArray: "3 7", lineCap: "round" },
      mixed: { dashArray: "14 6 3 6", lineCap: "butt" },
      other: { dashArray: "6 6", lineCap: "butt" },
    };
    return styles[normalizeKey(constructionType)] || styles.other;
  }

  function waterAssetLevelVisual(waterLevel) {
    return waterLevelVisuals[normalizeKey(waterLevel)] || null;
  }

  function waterAssetOutlineColor(assetType, status, waterLevel) {
    const normalizedStatus = normalizeKey(status);
    const levelVisual = waterAssetLevelVisual(waterLevel);
    if (storageWaterAssetTypes.has(assetType)) {
      if (normalizedStatus && normalizedStatus !== "operational") {
        return waterAssetStatusColor(normalizedStatus);
      }
      if (levelOutlineWaterAssetTypes.has(assetType) && levelVisual) {
        return levelVisual.outline;
      }
      if (normalizedStatus) {
        return waterAssetStatusColor(normalizedStatus);
      }
      return neutralWaterAssetColor;
    }
    return waterAssetStatusColor(normalizedStatus);
  }

  function waterAssetLabel(props, includeWaterLevel) {
    const bits = [];
    const name = String(props.name || "").trim();
    const assetTypeLabel = String(props.asset_type_label || "Water Asset").trim();
    const status = normalizeKey(props.status).replace(/_/g, " ");
    const waterLevel = normalizeKey(props.water_level).replace(/_/g, " ");

    if (name) {
      bits.push(name);
    }
    bits.push(assetTypeLabel);
    if (status) {
      bits.push("status " + status);
    }
    if (includeWaterLevel && waterLevel) {
      bits.push("water level " + waterLevel);
    }
    return bits.join(", ");
  }

  function statusWaterAssetSvg(props, code) {
    const fillColor = waterAssetStatusColor(props.status);
    return [
      '<svg class="water-asset-marker-svg" xmlns="http://www.w3.org/2000/svg" width="32" height="42" viewBox="0 0 32 42" role="img" aria-label="',
      escapeHtml(waterAssetLabel(props, false) || "Water asset"),
      '">',
      '<path d="',
      waterAssetMarkerPath,
      '" fill="',
      fillColor,
      '" stroke="#0F172A" stroke-width="1.5" stroke-linejoin="round"></path>',
      '<circle cx="16" cy="14.5" r="8.6" fill="#FFFFFF" fill-opacity="0.16"></circle>',
      '<circle cx="16" cy="14.5" r="8.6" fill="none" stroke="#FFFFFF" stroke-opacity="0.55" stroke-width="1"></circle>',
      '<text x="16" y="17.7" text-anchor="middle" font-family="',
      waterAssetFontFamily,
      '" font-size="10.8" font-weight="700" fill="#FFFFFF" letter-spacing="0.3">',
      escapeHtml(code),
      "</text>",
      "</svg>",
    ].join("");
  }

  function storageWaterAssetSvg(props, code) {
    const markerId = "water-asset-level-" + String((waterAssetMarkerSequence += 1));
    const levelVisual = waterAssetLevelVisual(props.water_level);
    const outlineColor = waterAssetOutlineColor(props.asset_type, props.status, props.water_level);
    const fillRatio = levelVisual ? levelVisual.ratio : 0;
    const dropletTop = 9;
    const dropletHeight = 14;
    const fillHeight = dropletHeight * fillRatio;
    const fillY = dropletTop + (dropletHeight - fillHeight);

    return [
      '<svg class="water-asset-marker-svg" xmlns="http://www.w3.org/2000/svg" width="32" height="42" viewBox="0 0 32 42" role="img" aria-label="',
      escapeHtml(waterAssetLabel(props, true) || "Water asset"),
      '">',
      "<defs>",
      '<clipPath id="',
      markerId,
      '"><rect x="10.4" y="',
      String(fillY.toFixed(2)),
      '" width="11.2" height="',
      String(fillHeight.toFixed(2)),
      '"></rect></clipPath>',
      "</defs>",
      '<path d="',
      waterAssetMarkerPath,
      '" fill="#FFFFFF" stroke="',
      outlineColor,
      '" stroke-width="1.8" stroke-linejoin="round"></path>',
      '<circle cx="16" cy="14.5" r="8.8" fill="#F8FAFC" fill-opacity="0.98" stroke="',
      outlineColor,
      '" stroke-opacity="0.18" stroke-width="1"></circle>',
      '<path d="',
      waterDropPath,
      '" fill="#EFF6FF"></path>',
      fillHeight > 0
        ? '<path d="' + waterDropPath + '" fill="#2563EB" clip-path="url(#' + markerId + ')"></path>'
        : "",
      '<path d="',
      waterDropPath,
      '" fill="none" stroke="',
      outlineColor,
      '" stroke-width="1.25" stroke-linejoin="round"></path>',
      '<text x="16" y="29.4" text-anchor="middle" font-family="',
      waterAssetFontFamily,
      '" font-size="7.5" font-weight="800" fill="',
      outlineColor,
      '" letter-spacing="0.5">',
      escapeHtml(code),
      "</text>",
      "</svg>",
    ].join("");
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
    if (fenceFocusMode) {
      return props.feature_type === "paddock" || props.feature_type === "farm_boundary" || props.feature_type === "fence_section";
    }
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
        color: fenceFocusMode ? "#94a3b8" : "#2b5d4f",
        weight: fenceFocusMode ? 1.2 : 2.4,
        fill: false,
        opacity: fenceFocusMode ? 0.55 : 0.95,
        interactive: !fenceFocusMode,
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

    if (props.feature_type === "fence_section") {
      const fenceId = String(props.fence_section_id || props.id || "");
      const selected = fenceFocusMode && selectedFenceId && fenceId === selectedFenceId;
      const critical = normalizeKey(props.condition) === "critical";
      const constructionStyle = fenceConstructionLineStyle(props.construction_type);
      return {
        color: selected ? "#111827" : fenceConditionColor(props.condition),
        weight: selected ? 7 : critical ? 4.2 : fenceFocusMode ? 4 : 3.2,
        fill: false,
        opacity: selected ? 1 : 0.96,
        dashArray: constructionStyle.dashArray,
        lineCap: constructionStyle.lineCap,
        lineJoin: "round",
      };
    }

    if (props.feature_type === "paddock") {
      if (fenceFocusMode) {
        return {
          color: "#94a3b8",
          weight: 1.3,
          fillColor: "#f8fafc",
          fillOpacity: 0.16,
          opacity: 0.72,
          interactive: false,
        };
      }
      if (normalizeKey(props.water_alert_level) === "critical") {
        return {
          color: "#b91c1c",
          weight: 2.4,
          fillColor: "#fca5a5",
          fillOpacity: paddockFillMode === "outline" ? 0.2 : 0.56,
          opacity: 0.98,
        };
      }
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
      if (props.feature_type === "gate") {
        const paddockNames = Array.isArray(props.paddock_names) ? props.paddock_names : [];
        const paddockLabels = Array.isArray(props.paddock_labels) ? props.paddock_labels : paddockNames;
        const connectedText = paddockLabels.length
          ? paddockLabels.map((item) => escapeHtml(item)).join(" / ")
          : escapeHtml(props.name || "Gate");
        const gateId = props.gate_id || props.id || "";
        const status = normalizeKey(props.status) === "open" ? "open" : "closed";
        const statusText = status === "open" ? "Open" : "Closed";
        const nextStatus = status === "open" ? "closed" : "open";
        const actionText = status === "open" ? "Close Gate" : "Open Gate";
        const detailLink = props.gate_detail_url
          ? '<a class="btn btn-secondary" href="' + escapeHtml(props.gate_detail_url) + '">Open Details</a>'
          : "";
        const actionHtml = gateId
          ? [
              '<form class="map-gate-state-form" data-gate-id="',
              escapeHtml(gateId),
              '" data-target-status="',
              nextStatus,
              '" data-gate-detail-url="',
              escapeHtml(props.gate_detail_url || ""),
              '" data-gate-state-url="',
              escapeHtml(props.gate_state_url || ""),
              '">',
              '<div class="map-gate-state-choice" data-gate-state-choice></div>',
              '<div class="map-gate-state-error" data-gate-state-error role="alert"></div>',
              '<div class="map-popup-actions">',
              '<button type="submit" class="btn">',
              actionText,
              "</button>",
              detailLink,
              "</div>",
              "</form>",
            ].join("")
          : "";
        return [
          farmLine,
          "<strong>" + name + "</strong>",
          '<div class="map-popup-farm">' + connectedText + "</div>",
          '<table class="map-popup-table">',
          "<tr><td>Status</td><td>" + statusText + "</td></tr>",
          "<tr><td>Source</td><td>" + escapeHtml(props.source || "-") + "</td></tr>",
          "<tr><td>Shared Fence</td><td>" + formatNumber(props.shared_boundary_length_m, 1) + " m</td></tr>",
          "</table>",
          actionHtml,
        ].join("");
      }

      if (props.feature_type === "fence_section") {
        const paddockNames = Array.isArray(props.paddock_names) ? props.paddock_names : [];
        const connectedText = paddockNames.length
          ? paddockNames.map((item) => escapeHtml(item)).join(" / ")
          : escapeHtml(props.paddock_a_name || props.paddock_b_name || "-");
        const electricText = props.electric_wire ? "Yes" : "No";
        const tagsText = Array.isArray(props.tags) ? props.tags.join(", ") : props.tags_csv || "";
        const notesText = String(props.notes || "").trim();
        const detailLink = props.fence_detail_url
          ? '<a href="' + escapeHtml(props.fence_detail_url) + '">Open fence</a>'
          : "";
        return [
          farmLine,
          "<strong>" + name + "</strong>",
          '<div class="map-popup-farm">' + connectedText + "</div>",
          '<table class="map-popup-table">',
          "<tr><td>Type</td><td>" + escapeHtml(props.section_type_label || props.section_type || "-") + "</td></tr>",
          "<tr><td>Condition</td><td>" + escapeHtml(props.condition_label || props.condition || "-") + "</td></tr>",
          "<tr><td>Build</td><td>" + escapeHtml(props.construction_type_label || props.construction_type || "-") + "</td></tr>",
          "<tr><td>Height</td><td>" + escapeHtml(props.height_profile_label || props.height_profile || "-") + "</td></tr>",
          "<tr><td>Electric</td><td>" + electricText + "</td></tr>",
          "<tr><td>Length</td><td>" + formatNumber(props.length_m, 1) + " m</td></tr>",
          tagsText ? "<tr><td>Tags</td><td>" + escapeHtml(tagsText) + "</td></tr>" : "",
          "</table>",
          notesText ? "<strong>Notes</strong><br>" + escapeHtml(notesText) : "",
          detailLink,
        ].join("");
      }

      if (props.feature_type === "water_asset") {
        const servedPaddocks = Array.isArray(props.served_paddocks) ? props.served_paddocks : [];
        const servedText = servedPaddocks.length
          ? servedPaddocks.map((row) => escapeHtml(row.name)).join("<br>")
          : "None";
        const locationText = props.location_paddock_name ? escapeHtml(props.location_paddock_name) : "None";
        const reviewText = props.needs_review ? "Yes" : "No";
        const networkText = props.network_warning ? escapeHtml(props.network_warning) : "-";
        const openAssetLink =
          props.asset_workspace_url && props.id
            ? '<a href="' +
              escapeHtml(props.asset_workspace_url + "?open_asset_id=" + encodeURIComponent(String(props.id)) + "#water-assets") +
              '" data-open-water-asset-modal="' +
              escapeHtml(String(props.id)) +
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
          "<tr><td>Network</td><td>" + networkText + "</td></tr>",
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
            (row) => {
              const mobName = escapeHtml(row.mob_name || "Unnamed mob");
              const mobLabel = row.mob_url
                ? '<a href="' + escapeHtml(row.mob_url) + '">' + mobName + "</a>"
                : mobName;
              return (
                mobLabel +
                " (" +
                formatNumber(row.allocation_pct, 1) +
                "%, " +
                formatNumber(row.allocated_lsu, 2) +
                " LSU)"
              );
            }
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
    const waterWarningText = String(props.water_alert_message || "").trim();
    const waterWarningBlock = waterWarningText
      ? '<div class="map-water-alert"><strong>Water Warning</strong><br>' + escapeHtml(waterWarningText) + "</div>"
      : "";

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
      waterWarningBlock ? "<br>" + waterWarningBlock : "",
      detailLink ? "<br>" + detailLink : "",
    ].join("");
  }

  function bindWaterAlertTooltip(feature, geoLayer) {
    const props = (feature && feature.properties) || {};
    if (props.feature_type !== "paddock") {
      return;
    }
    const waterAlertMessage = String(props.water_alert_message || "").trim();
    if (!waterAlertMessage) {
      return;
    }
    geoLayer.bindTooltip(escapeHtml(waterAlertMessage), {
      sticky: true,
      direction: "top",
      className: "map-water-alert-tooltip",
    });
  }

  function getFullscreenElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }

  const fullscreenTarget = mapElement.closest("[data-map-fullscreen-root]") || mapElement;

  function requestMapFullscreen() {
    if (typeof fullscreenTarget.requestFullscreen === "function") {
      return fullscreenTarget.requestFullscreen();
    }
    if (typeof fullscreenTarget.webkitRequestFullscreen === "function") {
      return fullscreenTarget.webkitRequestFullscreen();
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

  const map = L.map(mapElement, { scrollWheelZoom: true });
  map.createPane("paddockLabelPane");
  map.getPane("paddockLabelPane").style.zIndex = "640";
  map.getPane("paddockLabelPane").style.pointerEvents = "none";
  map.createPane("stockFloatPane");
  map.getPane("stockFloatPane").style.zIndex = "650";
  map.getPane("stockFloatPane").style.pointerEvents = "none";
  map.createPane("gatePane");
  map.getPane("gatePane").style.zIndex = "670";

  const baseTileLayers = [];

  function addBaseLayers() {
    if (baseLayerMode === "satellite") {
      const imageryLayer = L.tileLayer(
        "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        {
          maxZoom: 20,
          zIndex: 1,
          attribution:
            "Powered by Esri | Sources: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
        }
      ).addTo(map);
      const roadsLayer = L.tileLayer(
        "https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}",
        {
          maxZoom: 20,
          zIndex: 2,
          attribution: "Roads: Esri",
        }
      ).addTo(map);
      const labelsLayer = L.tileLayer(
        "https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        {
          maxZoom: 20,
          zIndex: 3,
          attribution: "Labels: Esri",
        }
      ).addTo(map);
      baseTileLayers.push(imageryLayer, roadsLayer, labelsLayer);
      return;
    }

    const streetLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 20,
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);
    baseTileLayers.push(streetLayer);
  }

  addBaseLayers();

  map.setView([-32.9102, 25.449], 13);

  let fullscreenButton = null;
  let refreshButton = null;
  let labelToggleButton = null;
  let gateToggleButton = null;
  let addGateButton = null;
  let paddockLabelLayer = null;
  let stockFloatLayer = null;
  let mapFeatureLayer = null;
  let mapDataLayers = [];
  let gateLayer = null;
  let paddockOptions = configuredGatePaddockOptions.slice();
  let mapDataLoaded = false;
  let mapReloading = false;
  let labelsVisible = true;
  let gatesVisible = false;
  let gateAddMode = false;
  let suppressNextMapCreateClick = false;

  function paddockOptionsFromFeatures(features) {
    const seen = new Set();
    const rows = [];
    features.forEach((feature) => {
      const props = (feature && feature.properties) || {};
      const paddockId = props.paddock_id;
      if (props.feature_type !== "paddock" || !paddockId || seen.has(paddockId)) {
        return;
      }
      seen.add(paddockId);
      rows.push({
        id: paddockId,
        name: props.name || "Unnamed Camp",
        farmId: props.farm_id || "",
        farmName: props.farm_name || "",
        label: props.name || "Unnamed Camp",
      });
    });
    return rows.sort((left, right) => left.name.localeCompare(right.name));
  }

  function parseGatePaddockOptions(raw) {
    let rows = [];
    try {
      rows = JSON.parse(raw || "[]");
    } catch (error) {
      rows = [];
    }
    if (!Array.isArray(rows)) {
      return [];
    }
    return rows
      .map((row) => ({
        id: String((row && row.id) || "").trim(),
        name: String((row && row.name) || "").trim(),
        farmId: String((row && row.farm_id) || "").trim(),
        farmName: String((row && row.farm_name) || "").trim(),
        label: String((row && (row.label || row.name)) || "").trim(),
      }))
      .filter((row) => row.id && row.name);
  }

  function parseGateFarmOptions(raw) {
    let rows = [];
    try {
      rows = JSON.parse(raw || "[]");
    } catch (error) {
      rows = [];
    }
    if (!Array.isArray(rows)) {
      return [];
    }
    return rows
      .map((row) => ({
        id: String((row && row.id) || "").trim(),
        name: String((row && row.name) || "").trim(),
      }))
      .filter((row) => row.id && row.name);
  }

  function mergePaddockOptions(featureOptions, configuredOptions) {
    const merged = new Map();
    configuredOptions.forEach((row) => {
      if (!merged.has(row.id)) {
        merged.set(row.id, row);
      }
    });
    featureOptions.forEach((row) => {
      if (!merged.has(row.id)) {
        merged.set(row.id, row);
      }
    });
    return Array.from(merged.values());
  }

  function updateAddGateButton() {
    if (!addGateButton) {
      return;
    }
    const canCreate = Boolean(
      gateCreateUrl &&
        paddockOptions.length >= 2 &&
        (!gateRequiresFarmSelection || configuredGateFarmOptions.length > 0)
    );
    addGateButton.disabled = !canCreate;
    addGateButton.textContent = gateAddMode ? "Cancel Gate" : "Add Gate";
    addGateButton.setAttribute("aria-pressed", gateAddMode ? "true" : "false");
    addGateButton.setAttribute("aria-label", gateAddMode ? "Cancel gate creation" : "Add a gate on the map");
    addGateButton.title = canCreate
      ? "Add a gate on the map"
      : gateRequiresFarmSelection
        ? "At least one active farm and two active camps are required"
        : "At least two active camps are required";
  }

  function updateGateToggleButton() {
    if (!gateToggleButton) {
      return;
    }
    const hasGates = gateMarkersById.size > 0;
    gateToggleButton.disabled = !hasGates;
    gateToggleButton.textContent = gatesVisible ? "Gates Off" : "Gates On";
    gateToggleButton.setAttribute("aria-pressed", gatesVisible ? "true" : "false");
    gateToggleButton.setAttribute("aria-label", gatesVisible ? "Hide gates on the map" : "Show gates on the map");
    gateToggleButton.title = hasGates
      ? gatesVisible
        ? "Hide gates"
        : "Show gates"
      : "No gates are mapped yet";
  }

  function syncGateVisibility() {
    if (!gateLayer) {
      updateGateToggleButton();
      return;
    }
    if (gatesVisible) {
      if (!map.hasLayer(gateLayer)) {
        gateLayer.addTo(map);
      }
    } else if (map.hasLayer(gateLayer)) {
      map.removeLayer(gateLayer);
    }
    updateGateToggleButton();
  }

  function toggleGateVisibility() {
    if (!gateLayer || gateMarkersById.size === 0) {
      return;
    }
    gatesVisible = !gatesVisible;
    syncGateVisibility();
    setStatus(gatesVisible ? "Gates shown." : "Gates hidden.");
  }

  function setGateAddMode(enabled) {
    const nextMode = Boolean(
      enabled &&
        gateCreateUrl &&
        paddockOptions.length >= 2 &&
        (!gateRequiresFarmSelection || configuredGateFarmOptions.length > 0)
    );
    gateAddMode = nextMode;
    mapElement.classList.toggle("map-gate-add-mode", gateAddMode);
    if (gateAddMode) {
      setStatus("Click the map where the gate should sit.");
    } else {
      map.closePopup();
    }
    updateAddGateButton();
  }

  function paddockSelectOptions(selectedId) {
    return paddockOptions
      .map((paddock) => {
        const selected = paddock.id === selectedId ? " selected" : "";
        return '<option value="' + escapeHtml(paddock.id) + '"' + selected + ">" + escapeHtml(paddock.label || paddock.name) + "</option>";
      })
      .join("");
  }

  function farmSelectOptions(selectedId) {
    return configuredGateFarmOptions
      .map((farm) => {
        const selected = farm.id === selectedId ? " selected" : "";
        return '<option value="' + escapeHtml(farm.id) + '"' + selected + ">" + escapeHtml(farm.name) + "</option>";
      })
      .join("");
  }

  function showGateCreateError(form, message) {
    const error = form.querySelector("[data-gate-create-error]");
    if (error) {
      error.textContent = message || "";
    }
  }

  function showGateStateError(form, message) {
    const error = form.querySelector("[data-gate-state-error]");
    if (error) {
      error.textContent = message || "";
    }
  }

  function renderGateCloseChoices(form, requirements) {
    const container = form.querySelector("[data-gate-state-choice]");
    if (!container) {
      return;
    }
    const mobs = Array.isArray(requirements && requirements.mobs) ? requirements.mobs : [];
    const components = Array.isArray(requirements && requirements.components) ? requirements.components : [];
    if (!mobs.length || !components.length) {
      container.innerHTML = "";
      form.dataset.choicesLoaded = "1";
      return;
    }
    const componentOptions = components
      .map(
        (component) =>
          '<option value="' +
          escapeHtml(component.component_paddock_id || "") +
          '">' +
          escapeHtml(component.label || "Camp") +
          "</option>"
      )
      .join("");
    container.innerHTML = mobs
      .map(
        (mob) =>
          '<label class="map-gate-state-choice-row">' +
          escapeHtml(mob.mob_name || "Mob") +
          '<select name="closure_choice:' +
          escapeHtml(mob.mob_id || "") +
          '" required><option value="">Choose side</option>' +
          componentOptions +
          "</select></label>"
      )
      .join("");
    form.dataset.choicesLoaded = "1";
  }

  function gateClosureChoicesFromForm(form) {
    const choices = [];
    Array.from(form.elements).forEach((element) => {
      if (!element.name || !element.name.startsWith("closure_choice:")) {
        return;
      }
      const mobId = element.name.split(":")[1];
      if (mobId && element.value) {
        choices.push({
          mob_id: mobId,
          component_paddock_id: element.value,
        });
      }
    });
    return choices;
  }

  function updateGateMarkerAfterState(gate) {
    const marker = addOrUpdateGateFeature(gate, gateLayer);
    if (marker && marker.openPopup) {
      marker.openPopup();
    }
    return marker;
  }

  function bindGateStateForm(popup) {
    const element = popup.getElement();
    const form = element ? element.querySelector(".map-gate-state-form") : null;
    if (!form || form.dataset.bound === "1") {
      return;
    }
    form.dataset.bound = "1";
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      showGateStateError(form, "");
      const gateId = form.dataset.gateId;
      const targetStatus = form.dataset.targetStatus;
      const gateDetailDirectUrl = form.dataset.gateDetailUrl || "";
      const gateStateDirectUrl = form.dataset.gateStateUrl || "";
      const submitButton = form.querySelector('button[type="submit"]');
      if (submitButton) {
        submitButton.disabled = true;
      }
      const restoreSubmit = () => {
        if (submitButton) {
          submitButton.disabled = false;
        }
      };
      const postState = () => {
        setStatus(targetStatus === "open" ? "Opening gate..." : "Closing gate...");
        postGateState(gateId, targetStatus, gateClosureChoicesFromForm(form), gateStateDirectUrl)
          .then((payload) => {
            const movedCount = Number(payload.moved_mob_count || 0);
            const statusMessage =
              "Gate " +
              (targetStatus === "open" ? "opened" : "closed") +
              "; redistributed " +
              movedCount +
              " mob(s).";
            updateGateMarkerAfterState((payload && payload.gate) || {});
            setStatus(statusMessage);
            return loadMapData({ preserveView: true, userInitiated: true }).then(() => {
              setStatus(statusMessage);
            });
          })
          .catch((error) => {
            restoreSubmit();
            const message = error.message || "Gate state could not be saved.";
            showGateStateError(form, message);
            setStatus(message);
          });
      };

      if (targetStatus === "closed" && form.dataset.choicesLoaded !== "1") {
        setStatus("Checking gate close requirements...");
        fetchGateDetail(gateId, gateDetailDirectUrl)
          .then((payload) => {
            const requirements = (payload && payload.close_requirements) || {};
            if (requirements.requires_choices) {
              renderGateCloseChoices(form, requirements);
              restoreSubmit();
              setStatus("Choose where spread mobs should be placed before closing the gate.");
              if (submitButton) {
                submitButton.textContent = "Confirm Close";
              }
              return;
            }
            form.dataset.choicesLoaded = "1";
            postState();
          })
          .catch((error) => {
            restoreSubmit();
            const message = error.message || "Gate close requirements could not be loaded.";
            showGateStateError(form, message);
            setStatus(message);
          });
        return;
      }
      postState();
    });
  }

  function openGateCreatePopup(latlng, suggestedPaddockId) {
    if (
      !gateCreateUrl ||
      paddockOptions.length < 2 ||
      (gateRequiresFarmSelection && configuredGateFarmOptions.length < 1)
    ) {
      setStatus(
        gateRequiresFarmSelection
          ? "At least one active farm and two active camps are required before a gate can be added."
          : "At least two active camps are required before a gate can be added."
      );
      return;
    }
    const firstId = suggestedPaddockId || paddockOptions[0].id;
    const first = paddockOptions.find((paddock) => paddock.id === firstId) || paddockOptions[0];
    const second = paddockOptions.find((paddock) => paddock.id !== firstId) || paddockOptions[1];
    const secondId = second ? second.id : "";
    const selectedFarmId =
      (first && first.farmId) ||
      (second && second.farmId) ||
      (configuredGateFarmOptions[0] && configuredGateFarmOptions[0].id) ||
      "";
    const farmSelectHtml = gateRequiresFarmSelection
      ? '<label>Farm<select name="farm_id" required>' + farmSelectOptions(selectedFarmId) + "</select></label>"
      : "";
    const popup = L.popup({
      maxWidth: 320,
      closeOnClick: false,
    })
      .setLatLng(latlng)
      .setContent(
        [
          '<form class="map-gate-create-form">',
          "<strong>Add Gate</strong>",
          '<input type="hidden" name="latitude" value="' + escapeHtml(latlng.lat.toFixed(8)) + '" />',
          '<input type="hidden" name="longitude" value="' + escapeHtml(latlng.lng.toFixed(8)) + '" />',
          farmSelectHtml,
          '<label>Camp A<select name="paddock_a_id" required>',
          paddockSelectOptions(firstId),
          "</select></label>",
          '<label>Camp B<select name="paddock_b_id" required>',
          paddockSelectOptions(secondId),
          "</select></label>",
          '<div class="map-gate-create-error" data-gate-create-error role="alert"></div>',
          '<div class="map-popup-actions">',
          '<button type="submit" class="btn">Save Gate</button>',
          '<button type="button" class="btn btn-secondary" data-gate-create-cancel>Cancel</button>',
          "</div>",
          "</form>",
        ].join("")
      );
    map.once("popupopen", function (event) {
      if (event.popup !== popup) {
        return;
      }
      const element = popup.getElement();
      const form = element ? element.querySelector(".map-gate-create-form") : null;
      if (!form) {
        return;
      }
      const cancelButton = form.querySelector("[data-gate-create-cancel]");
      if (cancelButton) {
        cancelButton.addEventListener("click", function () {
          setGateAddMode(false);
        });
      }
      form.addEventListener("submit", function (submitEvent) {
        submitEvent.preventDefault();
        showGateCreateError(form, "");
        const paddockAId = form.elements.paddock_a_id.value;
        const paddockBId = form.elements.paddock_b_id.value;
        const farmId = form.elements.farm_id ? form.elements.farm_id.value : "";
        if (gateRequiresFarmSelection && !farmId) {
          showGateCreateError(form, "Choose the farm this gate belongs to.");
          return;
        }
        if (!paddockAId || !paddockBId || paddockAId === paddockBId) {
          showGateCreateError(form, "Choose two different camps.");
          return;
        }
        const submitButton = form.querySelector('button[type="submit"]');
        if (submitButton) {
          submitButton.disabled = true;
        }
        setStatus("Saving gate...");
        postGateCreate({
          paddock_a_id: paddockAId,
          paddock_b_id: paddockBId,
          farm_id: farmId || undefined,
          latitude: form.elements.latitude.value,
          longitude: form.elements.longitude.value,
        })
          .then((payload) => {
            const marker = addOrUpdateGateFeature((payload && payload.gate) || {}, gateLayer);
            gatesVisible = true;
            syncGateVisibility();
            setGateAddMode(false);
            setStatus("Gate added. Drag it to adjust the location.");
            if (marker && marker.openPopup) {
              marker.openPopup();
            }
          })
          .catch((error) => {
            if (submitButton) {
              submitButton.disabled = false;
            }
            const message = error.message || "Gate could not be created.";
            showGateCreateError(form, message);
            setStatus(message);
          });
      });
    });
    popup.openOn(map);
    setStatus("Choose the two camps for this gate.");
  }

  function setFencePanelStatus(message) {
    if (fencePanelStatus) {
      fencePanelStatus.textContent = message || "";
    }
    if (message) {
      setStatus(message);
    }
  }

  function fenceIdFromFeature(feature) {
    const props = (feature && feature.properties) || {};
    return String(props.fence_section_id || props.id || "");
  }

  function cloneGeometry(geometry) {
    return geometry ? JSON.parse(JSON.stringify(geometry)) : null;
  }

  function geometryCoordinateLines(geometry) {
    if (!geometry || !Array.isArray(geometry.coordinates)) {
      return [];
    }
    if (geometry.type === "LineString") {
      return [geometry.coordinates];
    }
    if (geometry.type === "MultiLineString") {
      return geometry.coordinates;
    }
    return [];
  }

  function geometryFromCoordinateLines(lines) {
    const cleanLines = lines
      .map((line) =>
        line
          .filter((point) => Array.isArray(point) && point.length >= 2)
          .map((point) => [Number(point[0]), Number(point[1])])
      )
      .filter((line) => line.length >= 2);
    return cleanLines.length === 1
      ? { type: "LineString", coordinates: cleanLines[0] }
      : { type: "MultiLineString", coordinates: cleanLines };
  }

  function latLngLinesFromGeometry(geometry) {
    return geometryCoordinateLines(geometry).map((line) =>
      line.map((point) => L.latLng(Number(point[1]), Number(point[0])))
    );
  }

  function fenceVertexIcon(className) {
    return L.divIcon({
      className: className + "-wrap",
      html: '<span class="' + className + '"></span>',
      iconSize: [18, 18],
      iconAnchor: [9, 9],
    });
  }

  function clearFenceEditLayers() {
    if (fenceEditLineLayer) {
      map.removeLayer(fenceEditLineLayer);
      fenceEditLineLayer = null;
    }
    if (fenceEditMarkerLayer) {
      map.removeLayer(fenceEditMarkerLayer);
      fenceEditMarkerLayer = null;
    }
  }

  function refreshFenceEditLine() {
    if (fenceEditLineLayer && fenceEditGeometry) {
      fenceEditLineLayer.setLatLngs(latLngLinesFromGeometry(fenceEditGeometry));
    }
  }

  function renderFenceEditGeometry() {
    clearFenceEditLayers();
    if (!fenceEditGeometry) {
      return;
    }
    fenceEditLineLayer = L.polyline(latLngLinesFromGeometry(fenceEditGeometry), {
      color: "#111827",
      weight: 6,
      opacity: 0.88,
      dashArray: "2 5",
    }).addTo(map);
    fenceEditMarkerLayer = L.layerGroup().addTo(map);
    const editLines = geometryCoordinateLines(fenceEditGeometry);
    const editPointCount = editLines.reduce((count, line) => count + line.length, 0);
    editLines.forEach((line, lineIndex) => {
      line.forEach((point, pointIndex) => {
        const marker = L.marker([point[1], point[0]], {
          draggable: true,
          autoPan: true,
          icon: fenceVertexIcon("fence-vertex-marker"),
        });
        const removeTooltip =
          editPointCount <= 2
            ? "Drag to move. Click to archive section."
            : line.length <= 2
              ? "Drag to move. Click to remove this line."
              : "Drag to move. Click to remove.";
        marker.bindTooltip(removeTooltip, { direction: "top" });
        marker.on("drag", function () {
          const latlng = marker.getLatLng();
          point[0] = Number(latlng.lng.toFixed(8));
          point[1] = Number(latlng.lat.toFixed(8));
          refreshFenceEditLine();
        });
        marker.on("dragend", function () {
          renderFenceEditGeometry();
          setFencePanelStatus("Point moved. Save the fence when ready.");
        });
        marker.on("click", function (event) {
          L.DomEvent.stop(event);
          if (editPointCount <= 2) {
            archiveSelectedFence();
            return;
          }
          if (line.length <= 2) {
            const nextLines = geometryCoordinateLines(fenceEditGeometry).filter((_, index) => index !== lineIndex);
            if (!nextLines.length) {
              archiveSelectedFence();
              return;
            }
            fenceEditGeometry = geometryFromCoordinateLines(nextLines);
            renderFenceEditGeometry();
            setFencePanelStatus("Fence line removed. Save the fence when ready.");
            return;
          }
          line.splice(pointIndex, 1);
          fenceEditGeometry = geometryFromCoordinateLines(geometryCoordinateLines(fenceEditGeometry));
          renderFenceEditGeometry();
          setFencePanelStatus("Point removed. Save the fence when ready.");
        });
        fenceEditMarkerLayer.addLayer(marker);
      });
      for (let index = 0; index < line.length - 1; index += 1) {
        const start = line[index];
        const end = line[index + 1];
        const midpoint = [(Number(start[0]) + Number(end[0])) / 2, (Number(start[1]) + Number(end[1])) / 2];
        const marker = L.marker([midpoint[1], midpoint[0]], {
          icon: fenceVertexIcon("fence-segment-marker"),
        });
        marker.bindTooltip("Click to add a point.", { direction: "top" });
        marker.on("click", function (event) {
          L.DomEvent.stop(event);
          line.splice(index + 1, 0, [Number(midpoint[0].toFixed(8)), Number(midpoint[1].toFixed(8))]);
          fenceEditGeometry = geometryFromCoordinateLines(geometryCoordinateLines(fenceEditGeometry));
          renderFenceEditGeometry();
          setFencePanelStatus("Point added. Drag it into place, then save.");
        });
        fenceEditMarkerLayer.addLayer(marker);
      }
    });
  }

  function clearFenceDrawLayers() {
    if (fenceDrawLayer) {
      map.removeLayer(fenceDrawLayer);
      fenceDrawLayer = null;
    }
    if (fenceDrawMarkerLayer) {
      map.removeLayer(fenceDrawMarkerLayer);
      fenceDrawMarkerLayer = null;
    }
  }

  function renderFenceDrawGeometry() {
    clearFenceDrawLayers();
    fenceDrawMarkerLayer = L.layerGroup().addTo(map);
    if (fenceDrawPoints.length >= 2) {
      fenceDrawLayer = L.polyline(
        [fenceDrawPoints.map((point) => L.latLng(point[1], point[0]))],
        { color: "#0f766e", weight: 5, opacity: 0.9, dashArray: "7 5" }
      ).addTo(map);
    }
    fenceDrawPoints.forEach((point, pointIndex) => {
      const marker = L.marker([point[1], point[0]], {
        draggable: true,
        autoPan: true,
        icon: fenceVertexIcon("fence-draw-marker"),
      });
      marker.bindTooltip("Drag to move. Click to remove.", { direction: "top" });
      marker.on("drag", function () {
        const latlng = marker.getLatLng();
        point[0] = Number(latlng.lng.toFixed(8));
        point[1] = Number(latlng.lat.toFixed(8));
        if (fenceDrawLayer) {
          fenceDrawLayer.setLatLngs([fenceDrawPoints.map((row) => L.latLng(row[1], row[0]))]);
        }
      });
      marker.on("dragend", renderFenceDrawGeometry);
      marker.on("click", function (event) {
        L.DomEvent.stop(event);
        fenceDrawPoints.splice(pointIndex, 1);
        renderFenceDrawGeometry();
        updateFencePanelState();
      });
      fenceDrawMarkerLayer.addLayer(marker);
    });
  }

  function fenceFormValue(name) {
    return fenceForm && fenceForm.elements[name] ? fenceForm.elements[name].value : "";
  }

  function setFenceFormValue(name, value) {
    if (fenceForm && fenceForm.elements[name]) {
      fenceForm.elements[name].value = value == null ? "" : String(value);
    }
  }

  function setFenceFormChecked(name, value) {
    if (fenceForm && fenceForm.elements[name]) {
      fenceForm.elements[name].checked = Boolean(value);
    }
  }

  function fenceTagsText(props) {
    if (props.tags_csv) {
      return props.tags_csv;
    }
    if (Array.isArray(props.tags)) {
      return props.tags.join(", ");
    }
    return "";
  }

  function syncFencePaddockBRequirement() {
    if (!fenceForm || !fenceForm.elements.paddock_b_id) {
      return;
    }
    const internal = fenceFormValue("section_type") === "internal";
    fenceForm.elements.paddock_b_id.required = internal;
    if (!internal) {
      fenceForm.elements.paddock_b_id.value = "";
    }
  }

  function populateFenceForm(props) {
    if (!fenceForm) {
      return;
    }
    setFenceFormValue("fence_section_id", props.fence_section_id || props.id || "");
    setFenceFormValue("name", props.name || "");
    setFenceFormValue("section_type", props.section_type || "boundary");
    setFenceFormValue("paddock_a_id", props.paddock_a_id || "");
    setFenceFormValue("paddock_b_id", props.paddock_b_id || "");
    setFenceFormValue("condition", props.condition || "unknown");
    setFenceFormValue("height_profile", props.height_profile || "low");
    setFenceFormValue("construction_type", props.construction_type || "high_strung_wire");
    setFenceFormValue("electric_wire_type", props.electric_wire_type || "");
    setFenceFormValue("tags", fenceTagsText(props));
    setFenceFormValue("notes", props.notes || "");
    setFenceFormChecked("electric_wire", props.electric_wire);
    syncFencePaddockBRequirement();
  }

  function resetFenceFormForDraw() {
    if (!fenceForm) {
      return;
    }
    fenceForm.reset();
    setFenceFormValue("farm_id", fenceFormValue("farm_id"));
    setFenceFormValue("fence_section_id", "");
    setFenceFormValue("section_type", "boundary");
    setFenceFormValue("condition", "unknown");
    setFenceFormValue("height_profile", "low");
    setFenceFormValue("construction_type", "high_strung_wire");
    setFenceFormValue("tags", "");
    setFenceFormValue("notes", "");
    syncFencePaddockBRequirement();
  }

  function fencePayloadFromForm() {
    const sectionType = fenceFormValue("section_type") || "boundary";
    return {
      farm_id: fenceFormValue("farm_id"),
      name: fenceFormValue("name"),
      section_type: sectionType,
      paddock_a_id: fenceFormValue("paddock_a_id"),
      paddock_b_id: sectionType === "internal" ? fenceFormValue("paddock_b_id") : "",
      condition: fenceFormValue("condition") || "unknown",
      height_profile: fenceFormValue("height_profile") || "low",
      construction_type: fenceFormValue("construction_type") || "high_strung_wire",
      electric_wire: Boolean(fenceForm && fenceForm.elements.electric_wire && fenceForm.elements.electric_wire.checked),
      electric_wire_type: fenceFormValue("electric_wire_type"),
      tags: fenceFormValue("tags"),
      notes: fenceFormValue("notes"),
    };
  }

  function updateFencePanelState() {
    const hasSelection = Boolean(selectedFenceId);
    const canSave = fenceDrawing ? fenceDrawPoints.length >= 2 : hasSelection;
    if (fenceNewButton) {
      fenceNewButton.disabled = fenceDrawing;
      fenceNewButton.textContent = fenceDrawing ? "Drawing..." : "Draw Fence";
    }
    if (fenceEditButton) {
      fenceEditButton.disabled = !hasSelection || fenceDrawing || fenceEditing;
      fenceEditButton.textContent = fenceEditing ? "Editing Points" : "Edit Points";
    }
    if (fenceSaveButton) {
      fenceSaveButton.disabled = !canSave;
    }
    if (fenceCancelButton) {
      fenceCancelButton.disabled = !fenceDrawing && !fenceEditing;
    }
    if (fenceArchiveButton) {
      fenceArchiveButton.disabled = !hasSelection || fenceDrawing;
    }
  }

  function refreshFenceStyles() {
    fenceLayersById.forEach((layer) => {
      if (layer.setStyle && layer.feature) {
        layer.setStyle(styleForFeature(layer.feature));
        if (fenceIdFromFeature(layer.feature) === selectedFenceId && layer.bringToFront) {
          layer.bringToFront();
        }
      }
    });
  }

  function refreshFenceRegisterSelection() {
    fenceRegisterRowsById.forEach((row, fenceId) => {
      row.classList.toggle("is-selected-fence", fenceId === selectedFenceId);
    });
  }

  function stopFenceEditing(message) {
    fenceEditing = false;
    fenceEditGeometry = null;
    clearFenceEditLayers();
    if (message) {
      setFencePanelStatus(message);
    }
    updateFencePanelState();
  }

  function stopFenceDrawing(message) {
    fenceDrawing = false;
    fenceDrawPoints = [];
    mapElement.classList.remove("map-fence-draw-mode");
    clearFenceDrawLayers();
    if (message) {
      setFencePanelStatus(message);
    }
    updateFencePanelState();
  }

  function clearFenceSelection() {
    selectedFenceId = "";
    selectedFenceFeature = null;
    selectedFenceLayer = null;
    if (fenceDetailLink) {
      fenceDetailLink.hidden = true;
      fenceDetailLink.href = "#";
    }
    if (fencePanelTitle) {
      fencePanelTitle.textContent = "Fence Editor";
    }
    refreshFenceStyles();
    refreshFenceRegisterSelection();
    updateFencePanelState();
  }

  function selectFenceById(fenceId, options) {
    if (!fenceFocusMode || !fenceId) {
      return;
    }
    if (fenceDrawing) {
      stopFenceDrawing();
    }
    if (fenceEditing) {
      stopFenceEditing();
    }
    const layer = fenceLayersById.get(String(fenceId));
    if (!layer || !layer.feature) {
      setFencePanelStatus("Fence section is not visible on the map.");
      return;
    }
    selectedFenceId = String(fenceId);
    selectedFenceLayer = layer;
    selectedFenceFeature = layer.feature;
    const props = selectedFenceFeature.properties || {};
    populateFenceForm(props);
    if (fencePanelTitle) {
      fencePanelTitle.textContent = props.name || "Fence Editor";
    }
    if (fenceDetailLink) {
      fenceDetailLink.hidden = !props.fence_detail_url;
      fenceDetailLink.href = props.fence_detail_url || "#";
    }
    refreshFenceStyles();
    refreshFenceRegisterSelection();
    updateFencePanelState();
    if (options && options.fit && layer.getBounds) {
      const bounds = layer.getBounds();
      if (bounds.isValid()) {
        map.fitBounds(bounds.pad(0.24));
      }
    }
    setFencePanelStatus("Fence selected.");
  }

  function beginFencePointEdit() {
    if (!selectedFenceFeature || !selectedFenceFeature.geometry) {
      setFencePanelStatus("Choose a mapped fence section before editing points.");
      return;
    }
    if (fenceDrawing) {
      stopFenceDrawing();
    }
    fenceEditing = true;
    fenceEditGeometry = cloneGeometry(selectedFenceFeature.geometry);
    renderFenceEditGeometry();
    updateFencePanelState();
    setFencePanelStatus("Drag points, click blue markers to add points, or click white points to remove them.");
  }

  function beginFenceDraw() {
    if (!fenceFocusMode) {
      return;
    }
    if (fenceEditing) {
      stopFenceEditing();
    }
    clearFenceSelection();
    resetFenceFormForDraw();
    fenceDrawing = true;
    fenceDrawPoints = [];
    map.closePopup();
    mapElement.classList.add("map-fence-draw-mode");
    renderFenceDrawGeometry();
    updateFencePanelState();
    setFencePanelStatus("Click the map to place fence points.");
  }

  function validateFencePayload(payload) {
    if (!String(payload.name || "").trim()) {
      throw new Error("Fence name is required.");
    }
    if (!payload.paddock_a_id) {
      throw new Error("Paddock A is required.");
    }
    if (payload.section_type === "internal" && !payload.paddock_b_id) {
      throw new Error("Paddock B is required for internal fences.");
    }
    if (payload.section_type === "internal" && payload.paddock_a_id === payload.paddock_b_id) {
      throw new Error("Internal fences must connect two different paddocks.");
    }
  }

  function featureFromFenceResponse(payload) {
    return payload && payload.feature ? payload.feature : null;
  }

  function upsertFenceFeature(feature) {
    const fenceId = fenceIdFromFeature(feature);
    if (!fenceId || !mapFeatureLayer) {
      return null;
    }
    const existingLayer = fenceLayersById.get(fenceId);
    if (existingLayer) {
      mapFeatureLayer.removeLayer(existingLayer);
      fenceLayersById.delete(fenceId);
    }
    mapFeatureLayer.addData(feature);
    return fenceLayersById.get(fenceId) || null;
  }

  function fenceTagsHtml(fence) {
    const tags = Array.isArray(fence.tags)
      ? fence.tags
      : String(fence.tags_csv || "")
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean);
    if (!tags.length) {
      return '<span class="muted">-</span>';
    }
    return tags.map((tag) => '<span class="tag-pill">' + escapeHtml(tag) + "</span>").join(" ");
  }

  function fenceRegisterRowHtml(fence, detailUrl, taskCount) {
    const sectionType = fence.section_type_label || fence.section_type || "-";
    const condition = fence.condition || "unknown";
    const conditionLabel = fence.condition_label || condition;
    const heightLabel = fence.height_profile_label || fence.height_profile || "-";
    const constructionLabel = fence.construction_type_label || fence.construction_type || "-";
    const lengthText = fence.length_m === null || fence.length_m === undefined ? "-" : Number(fence.length_m).toFixed(1) + " m";
    const paddockNames = Array.isArray(fence.paddock_names) ? fence.paddock_names.join(" / ") : "";
    return [
      "<td>",
      '<a href="' + escapeHtml(detailUrl || "#") + '">' + escapeHtml(fence.name || "Fence Section") + "</a>",
      '<div class="muted">' + escapeHtml(paddockNames) + "</div>",
      "</td>",
      "<td>" + escapeHtml(sectionType) + "</td>",
      '<td><span class="status-pill status-' + escapeHtml(condition) + '">' + escapeHtml(conditionLabel) + "</span></td>",
      "<td>" + escapeHtml(heightLabel) + " / " + escapeHtml(constructionLabel) + "</td>",
      "<td>" + escapeHtml(lengthText) + "</td>",
      "<td>" + (fence.electric_wire ? "Yes" : "No") + "</td>",
      "<td>" + fenceTagsHtml(fence) + "</td>",
      "<td>",
      escapeHtml(taskCount),
      ' <button type="button" class="btn btn-secondary btn-small" data-fence-map-select="' +
        escapeHtml(fence.id || fence.fence_section_id || "") +
        '">Map</button>',
      "</td>",
    ].join("");
  }

  function upsertFenceRegisterRow(fence, feature) {
    if (!fenceRegisterBody || !fence) {
      return;
    }
    const fenceId = String(fence.id || fence.fence_section_id || "");
    if (!fenceId) {
      return;
    }
    const props = (feature && feature.properties) || {};
    const detailUrl = props.fence_detail_url || "#";
    let row = fenceRegisterRowsById.get(fenceId);
    if (!row) {
      row = document.createElement("tr");
      row.dataset.fenceRegisterRow = "";
      row.dataset.fenceSectionId = fenceId;
      row.dataset.taskCount = "0";
      fenceRegisterRowsById.set(fenceId, row);
      fenceRegisterBody.appendChild(row);
    }
    const taskCount = row.dataset.taskCount || String(fence.task_count || 0);
    row.dataset.taskCount = taskCount;
    row.innerHTML = fenceRegisterRowHtml(fence, detailUrl, taskCount);
  }

  function removeFenceRegisterRow(fenceId) {
    const row = fenceRegisterRowsById.get(String(fenceId));
    if (row && row.parentNode) {
      row.parentNode.removeChild(row);
    }
    fenceRegisterRowsById.delete(String(fenceId));
  }

  function saveFenceFromMap() {
    if (!fenceFocusMode || !fenceForm) {
      return;
    }
    let payload;
    try {
      payload = fencePayloadFromForm();
      validateFencePayload(payload);
      if (fenceDrawing) {
        if (fenceDrawPoints.length < 2) {
          throw new Error("Draw at least two points before saving.");
        }
        payload.geometry = { type: "LineString", coordinates: fenceDrawPoints };
      } else if (fenceEditing) {
        payload.geometry = cloneGeometry(fenceEditGeometry);
      }
    } catch (error) {
      setFencePanelStatus(error.message);
      return;
    }
    const selectedId = selectedFenceId;
    const saveUrl = fenceDrawing ? fenceCreateUrl : fenceUpdateUrl(selectedId);
    const method = fenceDrawing ? "POST" : "PATCH";
    if (fenceSaveButton) {
      fenceSaveButton.disabled = true;
    }
    setFencePanelStatus("Saving fence...");
    requestJson(saveUrl, method, payload)
      .then((body) => {
        const feature = featureFromFenceResponse(body);
        if (feature) {
          upsertFenceFeature(feature);
        }
        if (body && body.fence) {
          upsertFenceRegisterRow(body.fence, feature);
        }
        if (fenceDrawing) {
          stopFenceDrawing();
        }
        if (fenceEditing) {
          stopFenceEditing();
        }
        const fenceId = String((body && body.fence && (body.fence.id || body.fence.fence_section_id)) || fenceIdFromFeature(feature));
        if (fenceId) {
          selectFenceById(fenceId, { fit: false });
        }
        setFencePanelStatus("Fence saved.");
      })
      .catch((error) => {
        setFencePanelStatus(error.message || "Fence could not be saved.");
        updateFencePanelState();
      });
  }

  function archiveSelectedFence() {
    if (!selectedFenceId) {
      return;
    }
    if (!window.confirm("Archive this fence section? Notes, events, and linked tasks will remain.")) {
      return;
    }
    const fenceId = selectedFenceId;
    if (fenceArchiveButton) {
      fenceArchiveButton.disabled = true;
    }
    setFencePanelStatus("Archiving fence...");
    requestJson(fenceDeleteUrl(fenceId), "DELETE")
      .then(() => {
        const layer = fenceLayersById.get(fenceId);
        if (layer && mapFeatureLayer) {
          mapFeatureLayer.removeLayer(layer);
        }
        fenceLayersById.delete(fenceId);
        removeFenceRegisterRow(fenceId);
        stopFenceEditing();
        clearFenceSelection();
        setFencePanelStatus("Fence archived.");
      })
      .catch((error) => {
        setFencePanelStatus(error.message || "Fence could not be archived.");
        updateFencePanelState();
      });
  }

  function bindFenceMapUi() {
    if (!fenceFocusMode || !fenceForm) {
      return;
    }
    document.querySelectorAll("[data-fence-register-row]").forEach((row) => {
      const fenceId = row.dataset.fenceSectionId;
      if (fenceId) {
        fenceRegisterRowsById.set(String(fenceId), row);
      }
    });
    document.addEventListener("click", function (event) {
      const button = event.target.closest("[data-fence-map-select]");
      if (!button) {
        return;
      }
      event.preventDefault();
      selectFenceById(button.dataset.fenceMapSelect, { fit: true });
    });
    fenceForm.addEventListener("submit", function (event) {
      event.preventDefault();
      saveFenceFromMap();
    });
    fenceForm.elements.section_type.addEventListener("change", syncFencePaddockBRequirement);
    if (fenceNewButton) {
      fenceNewButton.addEventListener("click", beginFenceDraw);
    }
    if (fenceEditButton) {
      fenceEditButton.addEventListener("click", beginFencePointEdit);
    }
    if (fenceCancelButton) {
      fenceCancelButton.addEventListener("click", function () {
        if (fenceDrawing) {
          stopFenceDrawing("Drawing cancelled.");
          clearFenceSelection();
          return;
        }
        if (fenceEditing) {
          stopFenceEditing("Point editing cancelled.");
          if (selectedFenceFeature) {
            populateFenceForm(selectedFenceFeature.properties || {});
          }
        }
      });
    }
    if (fenceArchiveButton) {
      fenceArchiveButton.addEventListener("click", archiveSelectedFence);
    }
    syncFencePaddockBRequirement();
    updateFencePanelState();
  }

  function isMapFullscreen() {
    const activeElement = getFullscreenElement();
    return activeElement === fullscreenTarget || activeElement === mapElement;
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

  function updateRefreshButton() {
    if (!refreshButton) {
      return;
    }
    const busyLabel = mapDataLoaded ? "Refreshing" : "Loading";
    refreshButton.textContent = mapReloading ? busyLabel : "Refresh";
    refreshButton.disabled = mapReloading;
    refreshButton.setAttribute("aria-label", mapReloading ? busyLabel + " map" : "Refresh map");
    refreshButton.setAttribute("aria-busy", mapReloading ? "true" : "false");
    refreshButton.title = mapReloading ? busyLabel + " map" : "Refresh map";
  }

  function redrawBaseLayers() {
    baseTileLayers.forEach((layer) => {
      if (layer && typeof layer.redraw === "function") {
        layer.redraw();
      }
    });
  }

  function captureMapView() {
    const center = map.getCenter();
    const zoom = map.getZoom();
    if (
      !center ||
      !Number.isFinite(center.lat) ||
      !Number.isFinite(center.lng) ||
      !Number.isFinite(zoom)
    ) {
      return null;
    }
    return {
      center: L.latLng(center.lat, center.lng),
      zoom: zoom,
    };
  }

  function clearMapDataLayers() {
    map.closePopup();
    if (gateAddMode) {
      setGateAddMode(false);
    }
    if (fenceDrawing) {
      stopFenceDrawing();
    }
    if (fenceEditing) {
      stopFenceEditing();
    }
    if (fenceFocusMode) {
      clearFenceSelection();
    }
    mapDataLayers.forEach((layer) => {
      if (layer && map.hasLayer(layer)) {
        map.removeLayer(layer);
      }
    });
    mapDataLayers = [];
    paddockLabelLayer = null;
    stockFloatLayer = null;
    mapFeatureLayer = null;
    gateLayer = null;
    gateMarkersById.clear();
    fenceLayersById.clear();
    paddockOptions = configuredGatePaddockOptions.slice();
    updateAddGateButton();
    updateGateToggleButton();
    updateLabelToggleButton();
  }

  function featureCollection(rows) {
    return {
      type: "FeatureCollection",
      features: rows,
    };
  }

  function renderMapPayload(payload, options) {
    const renderOptions = options || {};
    const allFeatures = Array.isArray(payload.features) ? payload.features : [];
    const gateFeatures = allFeatures.filter((feature) => {
      const props = (feature && feature.properties) || {};
      return props.feature_type === "gate";
    });
    const features = allFeatures.filter((feature) => {
      const props = (feature && feature.properties) || {};
      return props.feature_type !== "gate" && isFeatureVisible(feature);
    });
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

    clearMapDataLayers();
    paddockOptions = mergePaddockOptions(paddockOptionsFromFeatures(allFeatures), configuredGatePaddockOptions);
    updateAddGateButton();

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
    mapDataLayers.push(paddockLabelLayer);
    stockFloatLayer = showStockFloats ? L.layerGroup().addTo(map) : null;
    if (stockFloatLayer) {
      mapDataLayers.push(stockFloatLayer);
    }

    const pointToMapLayer = function (feature, latlng) {
      const props = (feature && feature.properties) || {};
      if (props.feature_type === "gate") {
        return gateMarker(feature, latlng);
      }
      if (props.feature_type === "water_asset") {
        return waterAssetMarker(feature, latlng);
      }
      return L.marker(latlng);
    };
    const bindFeatureInteractions = function (feature, geoLayer) {
      const props = (feature && feature.properties) || {};
      const passiveFenceContext =
        fenceFocusMode && (props.feature_type === "paddock" || props.feature_type === "farm_boundary");
      if (!passiveFenceContext) {
        geoLayer.bindPopup(popupHtml(feature.properties || {}), { maxWidth: 360 });
        bindWaterAlertTooltip(feature, geoLayer);
      }
      addPaddockNameLabel(feature, geoLayer, paddockLabelLayer);
      if (!fenceFocusMode) {
        addStockFloatMarker(feature, geoLayer, stockFloatLayer);
      }
      if (props.feature_type === "fence_section") {
        const fenceId = fenceIdFromFeature(feature);
        if (fenceId) {
          fenceLayersById.set(fenceId, geoLayer);
          geoLayer.on("click", function () {
            if (fenceDrawing) {
              return;
            }
            selectFenceById(fenceId);
          });
        }
      }
      if (!fenceFocusMode && props.feature_type === "paddock") {
        geoLayer.on("click", function (event) {
          if (!gateAddMode) {
            return;
          }
          suppressNextMapCreateClick = true;
          openGateCreatePopup(event.latlng, props.paddock_id);
        });
      }
    };

    let layer = null;
    let boundsLayer = null;
    if (fenceFocusMode) {
      const contextFeatures = features.filter((feature) => {
        const props = (feature && feature.properties) || {};
        return props.feature_type === "paddock" || props.feature_type === "farm_boundary";
      });
      const fenceFeatures = features.filter((feature) => {
        const props = (feature && feature.properties) || {};
        return props.feature_type === "fence_section";
      });
      const contextLayer = L.geoJSON(featureCollection(contextFeatures), {
        interactive: false,
        style: styleForFeature,
        onEachFeature: function (feature, geoLayer) {
          addPaddockNameLabel(feature, geoLayer, paddockLabelLayer);
        },
      }).addTo(map);
      layer = L.geoJSON(featureCollection(fenceFeatures), {
        style: styleForFeature,
        onEachFeature: bindFeatureInteractions,
      }).addTo(map);
      mapDataLayers.push(contextLayer, layer);
      boundsLayer = L.featureGroup([contextLayer, layer]);
    } else {
      layer = L.geoJSON(featureCollection(features), {
        style: styleForFeature,
        pointToLayer: pointToMapLayer,
        onEachFeature: bindFeatureInteractions,
      }).addTo(map);
      mapDataLayers.push(layer);
      boundsLayer = layer;
    }
    mapFeatureLayer = layer;
    gateMarkersById.clear();
    gateLayer = L.geoJSON(featureCollection(gateFeatures), {
      pointToLayer: function (feature, latlng) {
        return gateMarker(feature, latlng);
      },
      onEachFeature: function (feature, geoLayer) {
        geoLayer.bindPopup(popupHtml(feature.properties || {}), { maxWidth: 360 });
      },
    });
    mapDataLayers.push(gateLayer);
    syncGateVisibility();
    syncPaddockLabelVisibility();
    updateLabelToggleButton();

    const view = renderOptions.view || null;
    const bounds = boundsLayer.getBounds();
    if (view) {
      map.setView(view.center, view.zoom, { animate: false });
    } else if (bounds.isValid()) {
      map.fitBounds(bounds.pad(0.08));
    }

    const unmatched = Array.isArray(payload.unmatched_placemarks)
      ? payload.unmatched_placemarks.length
      : 0;
    const missing = Array.isArray(payload.paddocks_without_kml)
      ? payload.paddocks_without_kml.length
      : 0;
    const loadedLabel = renderOptions.userInitiated ? "Map refreshed" : "Map loaded";

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
      setStatus(loadedLabel + " with warnings. " + warningParts.join(" "));
    } else if (hasWaterAssetFilter && allWaterFeatureCount > 0 && visibleWaterFeatureCount === 0) {
      setStatus(loadedLabel + ". Current water asset filters hide all water features.");
    } else {
      setStatus(loadedLabel + ".");
    }
  }

  function loadMapData(options) {
    const loadOptions = options || {};
    if (mapReloading) {
      return Promise.resolve();
    }
    const view = loadOptions.preserveView ? captureMapView() : null;
    mapReloading = true;
    updateRefreshButton();
    setStatus(loadOptions.userInitiated ? "Refreshing map..." : "Loading map...");
    return fetch(dataUrl, { headers: { Accept: "application/json" }, cache: "no-store" })
      .then((response) => {
        if (!response.ok) {
          throw new Error("Map data is unavailable. Check instance/maps/<farm name>.kml.");
        }
        return response.json();
      })
      .then((payload) => {
        renderMapPayload(payload, {
          userInitiated: Boolean(loadOptions.userInitiated),
          view: view,
        });
        mapDataLoaded = true;
        refreshMapSize();
      })
      .catch((error) => {
        setStatus(error.message);
      })
      .finally(() => {
        mapReloading = false;
        updateRefreshButton();
      });
  }

  function refreshMapData() {
    if (mapReloading) {
      return;
    }
    refreshMapSize();
    redrawBaseLayers();
    loadMapData({ preserveView: mapDataLoaded, userInitiated: true });
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

  const GateToggleControl = L.Control.extend({
    options: {
      position: "topright",
    },
    onAdd: function () {
      const container = L.DomUtil.create("div", "leaflet-bar map-gate-toggle-control");
      gateToggleButton = L.DomUtil.create("button", "map-control-button map-gate-toggle", container);
      gateToggleButton.type = "button";
      updateGateToggleButton();
      L.DomEvent.disableClickPropagation(container);
      L.DomEvent.on(gateToggleButton, "click", function (event) {
        L.DomEvent.stop(event);
        toggleGateVisibility();
      });
      return container;
    },
  });

  const RefreshControl = L.Control.extend({
    options: {
      position: "topright",
    },
    onAdd: function () {
      const container = L.DomUtil.create("div", "leaflet-bar map-refresh-control");
      refreshButton = L.DomUtil.create("button", "map-control-button map-refresh-button", container);
      refreshButton.type = "button";
      updateRefreshButton();
      L.DomEvent.disableClickPropagation(container);
      L.DomEvent.on(refreshButton, "click", function (event) {
        L.DomEvent.stop(event);
        refreshMapData();
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

  const AddGateControl = L.Control.extend({
    options: {
      position: "topright",
    },
    onAdd: function () {
      const container = L.DomUtil.create("div", "leaflet-bar map-add-gate-control");
      addGateButton = L.DomUtil.create("button", "map-control-button map-add-gate", container);
      addGateButton.type = "button";
      updateAddGateButton();
      L.DomEvent.disableClickPropagation(container);
      L.DomEvent.on(addGateButton, "click", function (event) {
        L.DomEvent.stop(event);
        setGateAddMode(!gateAddMode);
      });
      return container;
    },
  });

  map.addControl(new LabelToggleControl());
  if (!fenceFocusMode) {
    map.addControl(new GateToggleControl());
    map.addControl(new AddGateControl());
  }
  map.addControl(new RefreshControl());
  map.addControl(new FullscreenControl());

  map.on("click", function (event) {
    if (fenceFocusMode && fenceDrawing) {
      fenceDrawPoints.push([Number(event.latlng.lng.toFixed(8)), Number(event.latlng.lat.toFixed(8))]);
      renderFenceDrawGeometry();
      updateFencePanelState();
      setFencePanelStatus(
        fenceDrawPoints.length < 2
          ? "Place at least one more point."
          : "Fence line ready. Add more points or save."
      );
      return;
    }
    if (!gateAddMode) {
      return;
    }
    if (suppressNextMapCreateClick) {
      suppressNextMapCreateClick = false;
      return;
    }
    openGateCreatePopup(event.latlng);
  });
  map.on("popupopen", function (event) {
    bindGateStateForm(event.popup);
  });
  bindFenceMapUi();

  document.addEventListener("fullscreenchange", function () {
    updateFullscreenButton();
    refreshMapSize();
  });
  document.addEventListener("webkitfullscreenchange", function () {
    updateFullscreenButton();
    refreshMapSize();
  });
  loadMapData();
})();
