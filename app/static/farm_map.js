(function () {
  const mapElement = document.getElementById("farm-map");
  if (!mapElement || typeof L === "undefined") {
    return;
  }

  const statusElement = document.getElementById("farm-map-status");
  const dataUrl = mapElement.dataset.url;
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

    if (props.feature_type === "paddock") {
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

    return [
      farmLine,
      "<strong>" + name + "</strong>",
      '<table class="map-popup-table">',
      "<tr><td>Status</td><td>" + escapeHtml(props.status || "-") + "</td></tr>",
      "<tr><td>Current LSU</td><td>" + formatNumber(props.current_lsu, 2) + "</td></tr>",
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

  const map = L.map(mapElement, { scrollWheelZoom: true });
  map.createPane("paddockLabelPane");
  map.getPane("paddockLabelPane").style.zIndex = "640";
  map.getPane("paddockLabelPane").style.pointerEvents = "none";
  map.createPane("stockFloatPane");
  map.getPane("stockFloatPane").style.zIndex = "650";
  map.getPane("stockFloatPane").style.pointerEvents = "none";

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  map.setView([-32.9102, 25.449], 13);

  let fullscreenButton = null;

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

  const FullscreenControl = L.Control.extend({
    options: {
      position: "topright",
    },
    onAdd: function () {
      const container = L.DomUtil.create("div", "leaflet-bar map-fullscreen-control");
      fullscreenButton = L.DomUtil.create("button", "map-fullscreen-toggle", container);
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

  map.addControl(new FullscreenControl());

  document.addEventListener("fullscreenchange", function () {
    updateFullscreenButton();
    refreshMapSize();
  });
  document.addEventListener("webkitfullscreenchange", function () {
    updateFullscreenButton();
    refreshMapSize();
  });

  fetch(dataUrl, { headers: { Accept: "application/json" } })
    .then((response) => {
      if (!response.ok) {
        throw new Error("Map data is unavailable. Check instance/maps/<farm name>.kml.");
      }
      return response.json();
    })
    .then((payload) => {
      const features = Array.isArray(payload.features) ? payload.features : [];
      const missingKmlFarms = Array.isArray(payload.missing_kml_farms)
        ? payload.missing_kml_farms.length
        : 0;
      const invalidKmlFarms = Array.isArray(payload.invalid_kml_farms)
        ? payload.invalid_kml_farms.length
        : 0;
      if (!features.length) {
        if (missingKmlFarms > 0 || invalidKmlFarms > 0) {
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

      const paddockLabelLayer = L.layerGroup().addTo(map);
      const stockFloatLayer = L.layerGroup().addTo(map);
      const layer = L.geoJSON(payload, {
        style: styleForFeature,
        onEachFeature: function (feature, geoLayer) {
          geoLayer.bindPopup(popupHtml(feature.properties || {}), { maxWidth: 360 });
          addPaddockNameLabel(feature, geoLayer, paddockLabelLayer);
          addStockFloatMarker(feature, geoLayer, stockFloatLayer);
        },
      }).addTo(map);

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

      if (unmatched > 0 || missing > 0 || missingKmlFarms > 0 || invalidKmlFarms > 0) {
        setStatus(
          "Map loaded with warnings. Missing farm KML files: " +
            missingKmlFarms +
            ". Invalid farm KML files: " +
            invalidKmlFarms +
            ". Unmatched KML shapes: " +
            unmatched +
            ". Paddocks without map geometry: " +
            missing +
            "."
        );
      } else {
        setStatus("Map loaded.");
      }
    })
    .catch((error) => {
      setStatus(error.message);
    });
})();
