(function () {
  const form = document.getElementById("weir-geometry-form");
  if (!form) return;
  const { asset, defaults } = JSON.parse(document.getElementById("weir-geometry-config").textContent);
  const svg = document.getElementById("weir-geometry-map");
  const namespace = "http://www.w3.org/2000/svg";
  const origin = asset.weir_polygon.coordinates[0][0];
  const xScale = Math.cos(origin[1] * Math.PI / 180) * 111195.08;
  const project = p => [(p[0] - origin[0]) * xScale, -(p[1] - origin[1]) * 111195.08];
  const unproject = p => [p[0] / xScale + origin[0], origin[1] - p[1] / 111195.08];
  const rings = asset.weir_polygon.coordinates.map(ring => ring.map(project));
  const xs = rings[0].map(p => p[0]), ys = rings[0].map(p => p[1]);
  const width = Math.max(...xs) - Math.min(...xs), height = Math.max(...ys) - Math.min(...ys);
  const margin = Math.max(width, height) * 0.08;
  const fitted = [Math.min(...xs) - margin, Math.min(...ys) - margin, width + 2 * margin, height + 2 * margin];
  let view = [...fitted];
  const initialLine = asset.weir_centerline || (defaults && defaults.centerline);
  let points = initialLine ? initialLine.coordinates.map(project) : [];
  if (!initialLine && defaults && defaults.wall) points = [project(defaults.wall)];
  let suggested = !asset.weir_centerline && Boolean(initialLine);
  let defaultMessage = defaults && defaults.message;
  let drawing = points.length < 2, dirty = false, drag = null;
  const status = document.getElementById("weir-drawing-status");
  const result = document.getElementById("weir-result");
  const waterStatus = document.getElementById("weir-water-status");
  let waterFillPath = "", waterFillKey, waterFillMessage = "";
  let waterFillTimer = null, waterFillController = null;
  function node(tag, attributes) {
    const element = document.createElementNS(namespace, tag);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
    svg.appendChild(element); return element;
  }
  function snap(p) {
    let nearest = p, best = Infinity;
    for (let i = 1; i < rings[0].length; i++) {
      const a = rings[0][i - 1], b = rings[0][i], dx = b[0] - a[0], dy = b[1] - a[1];
      const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)));
      const q = [a[0] + t * dx, a[1] + t * dy], distance = Math.hypot(q[0] - p[0], q[1] - p[1]);
      if (distance < best) { best = distance; nearest = q; }
    }
    return nearest;
  }
  function position(event) {
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(svg.getScreenCTM().inverse());
    return [point.x, point.y];
  }
  function geometryPayload() {
    return {
      weir_centerline: { type: "LineString", coordinates: points.map(unproject) },
      weir_wall_height_m: form.elements.height.value,
      weir_height_assumed: form.elements.assumed.checked
    };
  }
  function scheduleWaterFill() {
    const level = form.elements.preview_level;
    const valid = !drawing && points.length >= 2 && level.value !== ""
      && level.validity.valid && form.elements.height.validity.valid;
    const key = valid ? JSON.stringify({
      ...geometryPayload(), water_level_percent: level.value, is_overflowing: false, include_water_fill: true
    }) : null;
    if (key === waterFillKey) return;
    waterFillKey = key;
    waterFillPath = "";
    clearTimeout(waterFillTimer);
    if (waterFillController) waterFillController.abort();
    waterFillController = null;
    result.textContent = "";
    waterFillMessage = drawing ? "Finish a centreline to show the estimated water coverage."
      : !valid ? "Enter a valid wall depth and water depth from 0 to 100% to show water coverage."
        : "Updating water coverage…";
    if (key) waterFillTimer = setTimeout(() => previewWaterFill(key, false), 200);
  }
  function estimateText(data) {
    return data.capacity_m3 == null
      ? "Estimate unavailable: " + (data.volume_estimate_message || data.volume_estimate_status.replace(/_/g, " "))
      : "Capacity " + data.capacity_m3.toLocaleString() + " m³; volume "
        + (data.estimated_volume_m3 == null ? "unknown" : data.estimated_volume_m3.toLocaleString() + " m³")
        + (data.volume_estimate_status === "preliminary" ? " (preliminary height)." : ".");
  }
  async function previewWaterFill(key, showResult) {
    clearTimeout(waterFillTimer);
    if (!key) return;
    if (waterFillController) waterFillController.abort();
    const controller = new AbortController();
    waterFillController = controller;
    if (showResult) result.textContent = "Calculating…";
    try {
      const response = await fetch("/api/water-assets/" + encodeURIComponent(asset.id) + "/volume-preview", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: key, signal: controller.signal
      });
      const data = await response.json();
      if (controller !== waterFillController || key !== waterFillKey) return;
      if (!response.ok) throw new Error(data.error || "Unable to preview water coverage");
      waterFillPath = (data.water_fill || []).map(polygon =>
        "M" + polygon.map(point => project(point).join(",")).join(" L") + " Z"
      ).join(" ");
      waterFillMessage = data.water_fill == null
        ? "Water coverage unavailable: " + (data.volume_estimate_message || data.volume_estimate_status.replace(/_/g, " "))
        : "Dark blue: estimated water coverage at " + Number(JSON.parse(key).water_level_percent) + "% depth.";
      if (showResult) result.textContent = "Preview: " + estimateText(data);
    } catch (error) {
      if (controller !== waterFillController || error.name === "AbortError") return;
      waterFillPath = "";
      waterFillMessage = "Water coverage unavailable: " + error.message;
      if (showResult) result.textContent = error.message;
    } finally {
      if (controller === waterFillController) {
        waterFillController = null;
        render();
      }
    }
  }
  function render() {
    const focusedPoint = svg.contains(document.activeElement) ? document.activeElement.getAttribute("data-point") : null;
    scheduleWaterFill();
    svg.setAttribute("viewBox", view.join(" "));
    svg.replaceChildren();
    const radius = 9 / svg.getScreenCTM().a;
    const path = rings.map(ring => "M" + ring.map(p => p.join(",")).join(" L") + " Z").join(" ");
    node("path", { d: path, fill: "#c8e5ec", "fill-rule": "evenodd", stroke: "#167b99", "stroke-width": radius / 3 });
    const defs = node("defs", {});
    const clip = document.createElementNS(namespace, "clipPath");
    clip.id = "weir-water-clip";
    clip.setAttribute("clipPathUnits", "userSpaceOnUse");
    const clipOutline = document.createElementNS(namespace, "path");
    clipOutline.setAttribute("d", path);
    clipOutline.setAttribute("clip-rule", "evenodd");
    clip.appendChild(clipOutline);
    defs.appendChild(clip);
    node("path", { id: "weir-water-fill", d: waterFillPath, fill: "#176b9b", "fill-rule": "nonzero",
      "clip-path": "url(#weir-water-clip)", "pointer-events": "none" });
    waterStatus.textContent = waterFillMessage;
    if (asset.latitude != null && asset.longitude != null) {
      const hint = project([asset.longitude, asset.latitude]);
      node("circle", { cx: hint[0], cy: hint[1], r: radius, fill: "#b46b10" });
    }
    node("polyline", { points: points.map(p => p.join(",")).join(" "), fill: "none", stroke: "#a94424", "stroke-width": radius / 2 });
    points.forEach((p, index) => {
      const marker = node("circle", { cx: p[0], cy: p[1], r: radius, fill: index === 0 ? "#234e35" : "#a94424", "data-point": index, tabindex: 0, role: "button", "aria-label": index === 0 ? "Wall point" : "Centreline point " + (index + 1) });
      const title = document.createElementNS(namespace, "title"); title.textContent = index === 0 ? "Wall" : "Point " + (index + 1); marker.appendChild(title);
    });
    if (drawing) {
      const prompt = points.length
        ? "Click along the channel, then finish at the upstream boundary."
        : "Click the wall boundary to start.";
      status.textContent = (defaultMessage ? defaultMessage + " " : "") + prompt;
    } else {
      status.textContent = (suggested ? "Suggested centreline, not yet saved. " : "Line ready. ")
        + "Drag points to refine it, then preview or save.";
    }
    if (focusedPoint != null) {
      const marker = svg.querySelector('[data-point="' + focusedPoint + '"]');
      if (marker) marker.focus({ preventScroll: true });
    }
  }
  function finish() {
    if (points.length < 2) throw new Error("Trace at least a wall point and an upstream endpoint.");
    points[0] = snap(points[0]); points[points.length - 1] = snap(points[points.length - 1]);
    drawing = false; render();
  }
  svg.addEventListener("pointerdown", event => {
    const index = event.target.getAttribute("data-point");
    drag = { start: position(event), index: index == null ? null : Number(index), pan: event.shiftKey, moved: false, view: [...view] };
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener("pointermove", event => {
    if (!drag) return;
    const p = position(event);
    if (Math.hypot(p[0] - drag.start[0], p[1] - drag.start[1]) < view[2] / 1000 && !drag.moved) return;
    drag.moved = true;
    if (drag.pan) { view[0] += drag.start[0] - p[0]; view[1] += drag.start[1] - p[1]; }
    else if (drag.index != null) {
      points[drag.index] = drag.index === 0 || (!drawing && drag.index === points.length - 1) ? snap(p) : p;
      dirty = true;
    }
    render();
  });
  svg.addEventListener("pointerup", event => {
    if (drag && !drag.pan && drag.index == null && !drag.moved && drawing) {
      const p = position(event); points.push(points.length ? p : snap(p)); dirty = true; render();
    }
    drag = null;
  });
  svg.addEventListener("pointercancel", () => { drag = null; });
  svg.addEventListener("keydown", event => {
    const index = event.target.getAttribute("data-point");
    const direction = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
    if (index == null || !direction) return;
    event.preventDefault();
    const i = Number(index), step = Math.max(view[2], view[3]) / 500 * (event.shiftKey ? 10 : 1);
    const p = [points[i][0] + direction[0] * step, points[i][1] + direction[1] * step];
    points[i] = i === 0 || (!drawing && i === points.length - 1) ? snap(p) : p;
    dirty = true; render();
    svg.querySelector('[data-point="' + i + '"]').focus();
  });
  svg.addEventListener("wheel", event => {
    event.preventDefault(); const p = position(event), scale = event.deltaY > 0 ? 1.15 : 1 / 1.15;
    if (view[2] * scale < fitted[2] / 100 || view[2] * scale > fitted[2] * 4) return;
    view = [p[0] + (view[0] - p[0]) * scale, p[1] + (view[1] - p[1]) * scale, view[2] * scale, view[3] * scale]; render();
  }, { passive: false });
  document.getElementById("weir-fit").onclick = () => { view = [...fitted]; render(); };
  document.getElementById("weir-clear").onclick = () => { points = []; drawing = true; dirty = true; suggested = false; defaultMessage = null; render(); };
  document.getElementById("weir-undo").onclick = () => { points.pop(); drawing = true; dirty = true; render(); };
  document.getElementById("weir-finish").onclick = () => { try { finish(); } catch (error) { result.textContent = error.message; } };
  form.addEventListener("input", event => {
    if (event.target.name !== "preview_level") dirty = true;
    render();
  });
  async function submit(preview) {
    if (!form.reportValidity()) return;
    const buttons = form.querySelectorAll("button");
    try {
      finish();
      if (preview) {
        if (!waterFillKey) throw new Error("Enter a water depth from 0 to 100% to preview.");
        await previewWaterFill(waterFillKey, true);
        return;
      }
      const payload = geometryPayload();
      payload.longitude = payload.weir_centerline.coordinates[0][0];
      payload.latitude = payload.weir_centerline.coordinates[0][1];
      buttons.forEach(b => { b.disabled = true; }); result.textContent = "Calculating…";
      const response = await fetch("/api/water-assets/" + encodeURIComponent(asset.id), {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Unable to save geometry");
      dirty = false; suggested = false; render();
      result.textContent = "Geometry saved. " + estimateText(data);
    } catch (error) { result.textContent = error.message; }
    finally { buttons.forEach(b => { b.disabled = false; }); }
  }
  document.getElementById("weir-preview").onclick = () => submit(true);
  form.onsubmit = event => { event.preventDefault(); submit(false); };
  window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
  render();
})();
