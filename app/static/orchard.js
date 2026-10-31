/* Local metre coordinates: [x, y] in storage, [y, x] at the Leaflet boundary. */
(() => {
  "use strict";
  const root = document.getElementById("orchard-workspace");
  if (!root) return;
  const $ = selector => root.querySelector(selector);
  const panel = $("#orchard-panel-body");
  const api = root.dataset.api;
  let state, map, spacesLayer, waterLayer, previewLayer, rowLabels;
  let selection = {type: "browse", id: null};
  let dirty = false, busy = false, conflict = false, drawing = false, picking = null;
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
  const ll = point => [point[1], point[0]];
  const xy = latlng => [latlng.lng, latlng.lat];
  const round = value => Math.round(value * 1000000) / 1000000;
  const option = (value, text, selected) => `<option value="${escape(value)}"${selected ? " selected" : ""}>${escape(text)}</option>`;
  const field = (name, title, value = "", extra = "") => `<label>${escape(title)}<input name="${name}" value="${escape(value)}" ${extra}></label>`;
  const numeric = (name, title, value, extra = "") => field(name, title, value, `type="number" ${extra.includes('step=') ? '' : 'step="any"'} required ${extra}`);
  const pair = (a, b) => `<div class="orchard-pair">${a}${b}</div>`;
  const help = text => `<p class="orchard-help">${escape(text)}</p>`;
  const find = (collection, id) => state[collection].find(item => item.id === id);
  const plantAt = spaceId => state.plants.find(p => p.space_id === spaceId);
  const rowSpaces = rowId => state.spaces.filter(s => s.row_id === rowId).sort((a, b) => a.position - b.position);
  const spaceName = space => `${find("rows", space.row_id).name} · space ${rowSpaces(space.row_id).findIndex(s => s.id === space.id) + 1}`;
  const emptyOptions = current => state.spaces.filter(s => !plantAt(s.id) || s.id === current).map(s => option(s.id, `${spaceName(s)} (${s.x_m}, ${s.y_m} m)`, s.id === current)).join("");
  const defaultLabelPoint = row => {
    const first = rowSpaces(row.id)[0];
    return [first.x_m - 2.6, first.y_m];
  };
  const labelPoint = row => row.label_x_m === null ? defaultLabelPoint(row) : [row.label_x_m, row.label_y_m];
  const labelIcon = (name, editing = false) => L.divIcon({
    className: `orchard-row-label-marker${editing ? " editing" : ""}`,
    html: `<span class="orchard-row-label">${escape(name)}</span>`, iconSize: [0, 0], iconAnchor: [0, 0],
  });

  function status(text, error = false) {
    $("#orchard-status").textContent = text;
    $("#orchard-status").classList.toggle("error", error);
  }
  function setDirty(value) {
    dirty = value;
    $("#orchard-unsaved").hidden = !value;
    $("#orchard-discard").hidden = !value;
  }
  function mayDiscard() {
    return !busy && (!dirty || window.confirm("Discard your unsaved edits?"));
  }
  function selectedSpace() {
    if (selection.type === "space") return selection.id;
    return selection.type === "plant" ? find("plants", selection.id)?.space_id : null;
  }
  async function fetchJson(url, options) {
    const response = await fetch(url, {cache: "no-store", ...options});
    if (response.redirected) throw new Error("Your session expired. Sign in again before saving.");
    let data;
    try { data = await response.json(); } catch { throw new Error("The server returned an unexpected response. Your edits remain visible."); }
    if (!response.ok) {
      if (response.status === 409) conflict = true;
      throw new Error(data.error?.message || data.error || "Unable to save the orchard.");
    }
    return data;
  }
  async function save(path, method, payload, next = selection) {
    if (busy) return;
    if (conflict) { status("Refresh the orchard before saving again. Your unsaved edits remain visible.", true); return; }
    busy = true;
    const controls = [...root.querySelectorAll("button, input, select, textarea")].map(element => [element, element.disabled]);
    controls.forEach(([element]) => { element.disabled = true; });
    status("Saving…");
    try {
      state = await fetchJson(api + path, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify({...payload, revision: state.revision})});
      setDirty(false);
      selection = next;
      drawing = false; picking = null;
      render();
      renderPanel();
      status("Saved. The orchard is up to date.");
    } catch (error) { status(error.message, true); }
    finally {
      busy = false;
      controls.forEach(([element, disabled]) => { element.disabled = disabled; });
    }
  }
  function select(type, id = null, scroll = true) {
    if (!state || !mayDiscard()) return;
    setDirty(false);
    drawing = false; picking = null;
    selection = {type, id};
    render();
    renderPanel();
    if (scroll && window.innerWidth <= 760) $(".orchard-panel").scrollIntoView({behavior: "smooth", block: "start"});
  }
  function groups() {
    const grouped = new Map();
    state.plants.filter(p => p.status === "unassigned").forEach(p => {
      if (!grouped.has(p.name)) grouped.set(p.name, []);
      grouped.get(p.name).push(p);
    });
    return [...grouped.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }
  function render() {
    const s = state.summary;
    $("#orchard-summary").innerHTML = [[s.planted, "Planted"], [s.empty, "Empty"], [s.planned, "Planned"], [s.unassigned, "Available"]].map(([n, text]) => `<div><strong>${n}</strong>${text}</div>`).join("");
    spacesLayer.clearLayers(); waterLayer.clearLayers(); rowLabels.clearLayers(); previewLayer.clearLayers();
    const selected = selectedSpace();
    state.spaces.forEach(space => {
      const plant = plantAt(space.id);
      const kind = plant?.status || "empty";
      const name = plant?.name || "Empty";
      const symbol = kind === "planted" ? "●" : kind === "planned" ? "◇" : "＋";
      const marker = L.marker([space.y_m, space.x_m], {
        icon: L.divIcon({className: `orchard-marker ${kind}${space.id === selected ? " selected" : ""}`, html: `<span class="orchard-space-symbol">${symbol}</span><span class="orchard-space-name">${escape(name)}</span>`, iconSize: [44, 44], iconAnchor: [22, 22]}),
        title: `${spaceName(space)}: ${name} (${kind})`, keyboard: true,
      }).addTo(spacesLayer);
      marker.on("click", event => {
        if (busy) return;
        if (drawing || picking) { mapPoint(event.latlng); return; }
        select("space", space.id);
      });
    });
    state.rows.forEach(row => {
      if (selection.type === "label" && selection.id === row.id) return;
      L.marker(ll(labelPoint(row)), {icon: labelIcon(row.name), title: `Move name label: ${row.name}`}).addTo(rowLabels)
        .on("click", event => {
          if (busy) return;
          if (drawing || picking) mapPoint(event.latlng); else select("label", row.id);
        });
    });
    state.lines.forEach(line => {
      L.polyline(line.vertices.map(ll), {color: "#287fa8", weight: 3, opacity: .7, dashArray: line.schematic ? "6 5" : null}).addTo(waterLayer)
        .on("click", event => { if (busy) return; if (drawing || picking) mapPoint(event.latlng); else select("line", line.id); L.DomEvent.stopPropagation(event); });
    });
    state.emitters.forEach(emitter => {
      L.marker([emitter.y_m, emitter.x_m], {icon: emitterIcon(emitter.kind), title: `${emitter.kind} on ${find("lines", emitter.line_id).name}`}).addTo(waterLayer)
        .on("click", event => { if (busy) return; if (drawing || picking) mapPoint(event.latlng); else select("emitter", emitter.id); });
    });
    toggleWater(); updateLabels();
  }
  function emitterIcon(kind) {
    return L.divIcon({className: `orchard-emitter ${kind}`, html: kind === "sprinkler" ? "✳" : "◆", iconSize: [28, 28], iconAnchor: [14, 14]});
  }
  function updateLabels() {
    if (!map) return;
    $("#orchard-map").classList.toggle("orchard-hide-labels", !$("#orchard-labels").checked || map.getZoom() < 3.5);
    $("#orchard-map").classList.toggle("orchard-overview", map.getZoom() < 3);
    $("#orchard-map").style.setProperty("--orchard-symbol-scale", Math.min(1, Math.max(.3, map.options.crs.scale(map.getZoom()) / 12)));
    $("#orchard-map").style.setProperty("--orchard-label-width", `${Math.min(110, Math.max(55, 4 * map.options.crs.scale(map.getZoom()) - 6))}px`);
  }
  function toggleWater() {
    if (!waterLayer) return;
    if ($("#orchard-irrigation").checked) waterLayer.addTo(map); else map.removeLayer(waterLayer);
  }
  function fit() {
    const points = [...state.spaces.map(s => [s.y_m, s.x_m]), ...state.rows.map(row => ll(labelPoint(row))), ...state.lines.flatMap(line => line.vertices.map(ll))];
    if (points.length) map.fitBounds(L.latLngBounds(points), {padding: [60, 55], maxZoom: 4.5});
    else map.setView([0, 0], 4);
  }
  function renderPanel() {
    previewLayer.clearLayers();
    let title = "Inspect orchard", html = "";
    const {type, id} = selection;
    if (type === "browse") {
      html = help("Select a position on the map or use the lists below. Editing a layout records its position; it does not record a physical transplant.") +
        `<label>Planting position<select data-select="space"><option value="">Choose a space…</option>${state.spaces.map(s => option(s.id, `${spaceName(s)} — ${plantAt(s.id)?.name || "Empty"}`)).join("")}</select></label>` +
        `<label>Rows / columns<select data-select="row"><option value="">Choose a row…</option>${state.rows.map(r => option(r.id, r.name)).join("")}</select></label>` +
        `<label>Irrigation lines<select data-select="line"><option value="">Choose a line…</option>${state.lines.map(line => option(line.id, line.name)).join("")}</select></label>` +
        `<label>Emitters<select data-select="emitter"><option value="">Choose an emitter…</option>${state.emitters.map((e, i) => option(e.id, `${i + 1}. ${e.kind} · ${find("lines", e.line_id).name}`)).join("")}</select></label>`;
    } else if (type === "space") {
      const space = find("spaces", id);
      if (!space) { selection = {type: "browse", id: null}; renderPanel(); return; }
      title = spaceName(space);
      html = help(`Position: ${space.x_m}, ${space.y_m} m`) + `<button type="button" data-row="${space.row_id}" class="btn-secondary">Edit this row</button>`;
      const plant = plantAt(id);
      if (plant) html += plantForm(plant);
      else html += `<h3>Empty planting space</h3><form data-form="reserve" data-space="${id}"><label>Available stock<select name="plant_id" required><option value="">Choose a plant…</option>${groups().map(([name, plants]) => option(plants[0].id, `${name} · ${plants.length} available`)).join("")}</select></label><button type="submit">Plan in this space</button></form>` + help("This reserves a plant. Confirm planting only when it is in the ground.");
    } else if (type === "plant") {
      const plant = find("plants", id);
      if (!plant) { select("stock"); return; }
      title = "Plant details"; html = plantForm(plant);
    } else if (type === "stock") {
      title = "Plant stock";
      html = `<h3>Available to plan</h3><ul class="orchard-stock-list">${groups().map(([name, plants]) => `<li><span>${escape(name)} <strong>× ${plants.length}</strong></span><div class="orchard-stock-actions"><button type="button" data-plant="${plants[0].id}">Plan</button><button type="button" data-remove-plant="${plants[0].id}" class="orchard-danger" aria-label="Remove one ${escape(name)} from stock">Remove 1</button></div></li>`).join("") || "<li>No unassigned plants.</li>"}</ul>` +
        help("Remove 1 deletes one available plant record after confirmation. For a planned plant, open its details to remove it and free its reserved space.") +
        `<label>Planned plants<select data-select="plant"><option value="">Choose a planned plant…</option>${state.plants.filter(p => p.status === "planned").map(p => option(p.id, `${p.name} · ${spaceName(find("spaces", p.space_id))}`)).join("")}</select></label>` +
        `<details><summary>Add purchased plants</summary><form data-form="stock">${field("name", "Plant name / variety", "", 'required maxlength="160"')}${numeric("quantity", "Quantity", 1, 'min="1" max="1000" step="1"')}${field("purchased_on", "Purchase date", root.dataset.today, 'type="date"')}${field("supplier", "Supplier (optional)", "", 'maxlength="160"')}<button type="submit">Add plants</button></form></details>`;
    } else if (type === "row") {
      const row = id ? find("rows", id) : {name: `Row ${state.rows.length + 1}`, orientation: "horizontal", x_m: 0, y_m: state.spaces.length ? Math.max(...state.spaces.map(s => s.y_m)) + 3 : 0, spacing_m: 4};
      title = id ? "Edit row / column" : "Add row / column";
      html = (id ? `<button type="button" data-label="${id}" class="btn-secondary">Move name label</button>` : "") +
        `<form data-form="row" data-id="${id || ""}">${field("name", "Name", row.name, 'required maxlength="120"')}<label>Direction<select name="orientation">${option("horizontal", "Horizontal · left to right", row.orientation === "horizontal")}${option("vertical", "Vertical · bottom to top", row.orientation === "vertical")}</select></label>${pair(numeric("x_m", "Origin x (m)", row.x_m), numeric("y_m", "Origin y (m)", row.y_m))}${numeric("spacing_m", "Distance between spaces (m)", row.spacing_m, 'min="0.001"')}` +
        (id ? `<p class="orchard-help">${rowSpaces(id).length} spaces. Drag the origin handle or edit coordinates. Extending the start preserves the origin and existing space identities.</p><label>Change length<select name="resize"><option value="">Keep current length</option><option value="extend:start">Extend start</option><option value="extend:end">Extend end</option><option value="trim:start">Remove empty start spaces</option><option value="trim:end">Remove empty end spaces</option></select></label>${numeric("count", "Spaces to add / remove", 1, 'min="1" max="1000" step="1"')}` : numeric("count", "Number of spaces", 11, 'min="1" max="1000" step="1"')) +
        `<button type="button" data-pick="row" class="btn-secondary">Pick origin on map</button><div class="orchard-actions"><button type="submit">${id ? "Save row" : "Create row"}</button>${id ? '<button type="button" data-delete="row" class="orchard-danger">Delete row</button>' : ""}</div></form>` + help("Row changes are layout corrections. Irrigation routes remain independently positioned.");
    } else if (type === "label") {
      const row = find("rows", id);
      const point = labelPoint(row);
      title = "Move row / column name";
      html = help(`Move the “${row.name}” label. Planting spaces and irrigation stay in place. Drag the label on the map, pick a position or edit its coordinates, then save.`) +
        `<form data-form="label" data-id="${id}">${pair(numeric("x_m", "Label x (m)", point[0]), numeric("y_m", "Label y (m)", point[1]))}<label><input type="checkbox" name="automatic" ${row.label_x_m === null ? "checked" : ""}> Follow the row start automatically</label><button type="button" data-pick="label" class="btn-secondary">Pick label position on map</button><div class="orchard-actions"><button type="submit">Save label position</button><button type="button" data-reset-label class="btn-secondary">Reset to default</button></div></form>`;
    } else if (type === "line") {
      const line = id ? find("lines", id) : {name: `Irrigation line ${state.lines.length + 1}`, vertices: [], schematic: true};
      title = id ? "Edit irrigation line" : "Draw irrigation line";
      html = `<form data-form="line" data-id="${id || ""}">${field("name", "Line name", line.name, 'required maxlength="120"')}<label><input type="checkbox" name="schematic" ${line.schematic ? "checked" : ""}> Schematic route (not verified on the ground)</label><label>Vertices in metres · one x, y pair per line<textarea name="vertices" spellcheck="false">${escape(line.vertices.map(p => p.join(", ")).join("\n"))}</textarea></label><div class="orchard-actions"><button type="button" data-draw="start" class="btn-secondary">Add points on map</button><button type="button" data-draw="finish" class="btn-secondary">Finish drawing</button><button type="button" data-preview class="btn-secondary">Preview route</button></div><div class="orchard-actions"><button type="submit">Save line</button>${id ? '<button type="button" data-delete="line" class="orchard-danger">Delete line</button><button type="button" data-new-emitter class="btn-secondary">Add emitter</button>' : ""}</div></form>` + help("Drag vertex handles or edit the coordinate list to change or remove points. Emitters are projected to the nearest point on the edited route; review their preview before saving.");
    } else if (type === "emitter" || type === "new-emitter") {
      const emitter = type === "emitter" ? find("emitters", id) : null;
      const line = find("lines", emitter ? emitter.line_id : id);
      title = emitter ? "Edit emitter" : "Place emitter";
      html = help(line.name) + `<form data-form="emitter" data-id="${emitter?.id || ""}" data-line="${line.id}"><label>Type<select name="kind">${option("dripper", "Dripper", !emitter || emitter.kind === "dripper")}${option("sprinkler", "Sprinkler", emitter?.kind === "sprinkler")}</select></label>${pair(numeric("x_m", "x (m)", emitter?.x_m ?? line.vertices[0][0]), numeric("y_m", "y (m)", emitter?.y_m ?? line.vertices[0][1]))}<button type="button" data-pick="emitter" class="btn-secondary">Pick position on map</button><div class="orchard-actions"><button type="submit">Save emitter</button>${emitter ? '<button type="button" data-delete="emitter" class="orchard-danger">Delete emitter</button>' : ""}</div></form>` + help("The marker snaps to this line. Drag the preview marker or enter coordinates, then save.");
    }
    $("#orchard-panel-title").textContent = title;
    panel.innerHTML = html;
    updatePlantFields(); preview();
  }
  function plantForm(plant) {
    const position = plant.space_id ? spaceName(find("spaces", plant.space_id)) : "Not assigned";
    const actions = plant.status === "unassigned" ? [["reserve", "Reserve a planting space"], ["name", "Update name only"]] : plant.status === "planned" ? [["plant", "Confirm planting completed"], ["reserve", "Move planned placement"], ["cancel", "Cancel reservation"], ["name", "Update name only"]] : [["name", "Update name only"], ["correct", "Correct recorded location"]];
    return `<dl><dt>Status / location</dt><dd>${escape(plant.status)} · ${escape(position)}</dd><dt>Purchased</dt><dd>${escape(plant.purchased_on || "Unknown")}${plant.supplier ? ` · ${escape(plant.supplier)}` : ""}</dd><dt>Planted</dt><dd>${escape(plant.planted_on || "Unknown / not recorded")}</dd></dl><form data-form="plant" data-id="${plant.id}">${field("name", "Plant name / variety", plant.name, 'required maxlength="160"')}<label>Action<select name="action">${actions.map(([value, name]) => option(value, name)).join("")}</select></label><label data-destination>Planting space<select name="space_id">${emptyOptions(plant.space_id)}</select></label><label data-plant-date>Planting completed on<input type="date" name="planted_on" value="${root.dataset.today}"></label><button type="submit">Save plant</button></form>` + help("Location correction changes the record only. A reservation stays unplanted until you confirm planting.") +
      (plant.status !== "planted" ? `<button type="button" data-remove-plant="${plant.id}" class="orchard-danger">Remove from stock</button>` : "");
  }
  function updatePlantFields() {
    const form = panel.querySelector('[data-form="plant"]');
    if (!form) return;
    const action = form.elements.action.value;
    form.querySelector("[data-destination]").hidden = !["reserve", "correct"].includes(action);
    form.querySelector("[data-plant-date]").hidden = action !== "plant";
  }
  function parseVertices(text, minimum = 2) {
    const lines = text.trim() ? text.trim().split(/\n+/) : [];
    const points = lines.map(line => {
      const parts = line.trim().split(/\s*,\s*|\s+/);
      if (parts.length !== 2 || parts.some(part => part === "" || !Number.isFinite(Number(part)))) throw new Error("Each line must contain two numbers: x, y.");
      return parts.map(Number);
    });
    if (points.length < minimum || points.length > 200) throw new Error(`Use between ${minimum} and 200 vertices.`);
    if (points.some((point, i) => i && point[0] === points[i - 1][0] && point[1] === points[i - 1][1])) throw new Error("Adjacent line vertices must be different.");
    return points;
  }
  function project(point, vertices) {
    let best = null, distance = Infinity;
    vertices.slice(1).forEach((b, index) => {
      const a = vertices[index], dx = b[0] - a[0], dy = b[1] - a[1];
      const fraction = Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy)));
      const candidate = [a[0] + fraction * dx, a[1] + fraction * dy];
      const d = (candidate[0] - point[0]) ** 2 + (candidate[1] - point[1]) ** 2;
      if (d < distance) { distance = d; best = candidate; }
    });
    return best;
  }
  function dragHandle(point, onEnd, icon = null) {
    return L.marker(ll(point), {draggable: true, icon: icon || L.divIcon({className: "orchard-handle", html: "↔", iconSize: [27, 27], iconAnchor: [13, 13]})})
      .addTo(previewLayer).on("dragend", event => { if (!busy) onEnd(xy(event.target.getLatLng()).map(round)); });
  }
  function preview(showErrors = false) {
    previewLayer.clearLayers();
    const form = panel.querySelector("form");
    if (!form) return;
    try {
      if (form.dataset.form === "line") {
        const points = parseVertices(form.elements.vertices.value, 0);
        if (points.length > 1) {
          L.polyline(points.map(ll), {color: "#105d97", weight: 4, dashArray: "4 4", interactive: false}).addTo(previewLayer);
          state.emitters.filter(e => e.line_id === selection.id).forEach(e => L.marker(ll(project([e.x_m, e.y_m], points)), {icon: emitterIcon(e.kind), interactive: false}).addTo(previewLayer));
        }
        points.forEach((point, index) => dragHandle(point, value => { points[index] = value; form.elements.vertices.value = points.map(p => p.join(", ")).join("\n"); setDirty(true); preview(); }));
      } else if (form.dataset.form === "label") {
        const point = [Number(form.elements.x_m.value), Number(form.elements.y_m.value)];
        if (!point.every(Number.isFinite)) return;
        const row = find("rows", form.dataset.id);
        dragHandle(point, value => {
          form.elements.x_m.value = value[0]; form.elements.y_m.value = value[1];
          form.elements.automatic.checked = false;
          setDirty(true); preview();
        }, labelIcon(row.name, true)).bindTooltip("Drag the name, then save its position.");
      } else if (form.dataset.form === "row") {
        const x = Number(form.elements.x_m.value), y = Number(form.elements.y_m.value), spacing = Number(form.elements.spacing_m.value);
        const vertical = form.elements.orientation.value === "vertical";
        let positions = selection.id ? rowSpaces(selection.id).map(s => s.position) : Array.from({length: Math.min(1000, Math.max(0, Number(form.elements.count.value)))}, (_, i) => i);
        if (selection.id && form.elements.resize.value) {
          const [action, end] = form.elements.resize.value.split(":");
          const count = Math.min(1000, Math.max(0, Math.trunc(Number(form.elements.count.value))));
          if (action === "trim") positions = end === "start" ? positions.slice(count) : positions.slice(0, Math.max(0, positions.length - count));
          else if (end === "start") positions = [...Array.from({length: count}, (_, i) => positions[0] - count + i), ...positions];
          else positions = [...positions, ...Array.from({length: count}, (_, i) => positions[positions.length - 1] + i + 1)];
        }
        if (![x, y, spacing].every(Number.isFinite) || spacing <= 0) return;
        const points = positions.map(p => [x + (vertical ? 0 : p * spacing), y + (vertical ? p * spacing : 0)]);
        if (points.length) L.polyline(points.map(ll), {color: "#a27628", dashArray: "4 4", interactive: false}).addTo(previewLayer);
        points.forEach(point => L.circleMarker(ll(point), {radius: 6, color: "#a27628", fillColor: "#fff3d5", fillOpacity: .8, weight: 1, interactive: false}).addTo(previewLayer));
        dragHandle([x, y], point => { form.elements.x_m.value = point[0]; form.elements.y_m.value = point[1]; setDirty(true); preview(); });
      } else if (form.dataset.form === "emitter") {
        const point = [Number(form.elements.x_m.value), Number(form.elements.y_m.value)];
        if (!point.every(Number.isFinite)) return;
        const line = find("lines", form.dataset.line);
        const snapped = project(point, line.vertices);
        dragHandle(snapped, value => { const p = project(value, line.vertices); form.elements.x_m.value = round(p[0]); form.elements.y_m.value = round(p[1]); setDirty(true); preview(); }, emitterIcon(form.elements.kind.value));
      }
    } catch (error) { if (showErrors) status(error.message, true); }
  }
  function resetLabel(form) {
    const point = defaultLabelPoint(find("rows", form.dataset.id));
    form.elements.x_m.value = point[0]; form.elements.y_m.value = point[1];
    form.elements.automatic.checked = true;
  }
  function mapPoint(latlng) {
    if (busy) return;
    const form = panel.querySelector("form");
    if (!form) return;
    const point = xy(latlng).map(round);
    if (drawing && form.dataset.form === "line") {
      try {
        const points = parseVertices(form.elements.vertices.value, 0);
        points.push(point);
        form.elements.vertices.value = points.map(p => p.join(", ")).join("\n");
        setDirty(true); preview(true);
        status("Point added. Add another point, or finish drawing and save.");
      } catch (error) { status(error.message, true); }
    } else if (picking) {
      form.elements.x_m.value = point[0]; form.elements.y_m.value = point[1];
      if (form.dataset.form === "label") form.elements.automatic.checked = false;
      picking = null; setDirty(true); preview(); status("Position selected. Review the preview, then save.");
    }
  }
  panel.addEventListener("input", event => {
    const el = event.target;
    if (el.form?.dataset.form === "label") {
      if (["x_m", "y_m"].includes(el.name)) el.form.elements.automatic.checked = false;
      if (el.name === "automatic" && el.checked) resetLabel(el.form);
    }
    if (el.closest("form")) { setDirty(true); preview(); }
  });
  panel.addEventListener("change", event => {
    const el = event.target;
    if (el.dataset.select && el.value) { select(el.dataset.select, el.value); return; }
    if (!el.closest("form")) return;
    if (el.name === "orientation" && !selection.id) el.form.elements.spacing_m.value = el.value === "vertical" ? 3 : 4;
    setDirty(true); updatePlantFields(); preview();
  });
  panel.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button || busy) return;
    if (button.dataset.row) select("row", button.dataset.row);
    if (button.dataset.plant) select("plant", button.dataset.plant);
    if (button.dataset.label) select("label", button.dataset.label);
    if (button.hasAttribute("data-reset-label")) { resetLabel(button.form); setDirty(true); preview(); }
    if (button.dataset.removePlant) {
      const plant = find("plants", button.dataset.removePlant);
      const purchase = `Purchase: ${plant.purchased_on || "unknown date"}${plant.supplier ? ` · ${plant.supplier}` : ""}.`;
      const placement = plant.status === "planned" ? ` Its reservation at ${spaceName(find("spaces", plant.space_id))} will be released.` : "";
      const message = `Remove one “${plant.name}” from stock? ${purchase}${placement} This permanently deletes this plant record.`;
      if (window.confirm(message + (dirty ? " Unsaved edits will be discarded if removal succeeds." : ""))) {
        save(`/plants/${plant.id}`, "DELETE", {}, selection.type === "space" ? selection : {type: "stock", id: null});
      }
    }
    if (button.dataset.pick) { picking = button.dataset.pick; status("Click or tap the desired position on the map, then review and save."); $("#orchard-map").scrollIntoView({behavior: "smooth", block: "center"}); }
    if (button.dataset.draw === "start") { drawing = true; status("Click or tap to add line vertices. Use Finish drawing when ready."); $("#orchard-map").scrollIntoView({behavior: "smooth", block: "center"}); }
    if (button.dataset.draw === "finish") { drawing = false; preview(true); status("Drawing finished. Review the route and projected emitters, then save."); }
    if (button.hasAttribute("data-preview")) preview(true);
    if (button.hasAttribute("data-new-emitter")) select("new-emitter", selection.id);
    if (button.dataset.delete) {
      const kind = button.dataset.delete;
      const message = kind === "line" ? "Delete this irrigation line and all its emitters?" : kind === "row" ? "Delete this row and its empty spaces? Occupied spaces are protected." : "Delete this emitter?";
      if (window.confirm(message + (dirty ? " Unsaved edits will be discarded if deletion succeeds." : ""))) save(`/${kind === "row" ? "rows" : kind === "line" ? "lines" : "emitters"}/${selection.id}`, "DELETE", {}, {type: "browse", id: null});
    }
  });
  panel.addEventListener("submit", event => {
    event.preventDefault();
    if (busy) return;
    const form = event.target, values = Object.fromEntries(new FormData(form));
    const type = form.dataset.form, id = form.dataset.id;
    try {
      if (type === "row") {
        const payload = {name: values.name, orientation: values.orientation, x_m: Number(values.x_m), y_m: Number(values.y_m), spacing_m: Number(values.spacing_m), count: Number(values.count)};
        if (id && values.resize) { const [action, end] = values.resize.split(":"); payload.resize = {action, end, count: Number(values.count)}; }
        save(`/rows${id ? `/${id}` : ""}`, id ? "PATCH" : "POST", payload, id ? selection : {type: "browse", id: null});
      } else if (type === "label") {
        save(`/rows/${id}`, "PATCH", {label_x_m: form.elements.automatic.checked ? null : Number(values.x_m), label_y_m: form.elements.automatic.checked ? null : Number(values.y_m)});
      } else if (type === "stock") save("/plants", "POST", {...values, quantity: Number(values.quantity)});
      else if (type === "reserve") save(`/plants/${values.plant_id}/placement`, "POST", {action: "reserve", space_id: form.dataset.space});
      else if (type === "plant") save(`/plants/${id}${values.action === "name" ? "" : "/placement"}`, values.action === "name" ? "PATCH" : "POST", values);
      else if (type === "line") {
        const vertices = parseVertices(values.vertices);
        preview(true);
        save(`/lines${id ? `/${id}` : ""}`, id ? "PATCH" : "POST", {name: values.name, vertices, schematic: form.elements.schematic.checked}, id ? selection : {type: "browse", id: null});
      } else if (type === "emitter") save(id ? `/emitters/${id}` : `/lines/${form.dataset.line}/emitters`, id ? "PATCH" : "POST", {kind: values.kind, x_m: Number(values.x_m), y_m: Number(values.y_m)}, id ? selection : {type: "line", id: form.dataset.line});
    } catch (error) { status(error.message, true); }
  });
  root.querySelectorAll("[data-open]").forEach(button => button.addEventListener("click", () => select(button.dataset.open)));
  $("#orchard-labels").addEventListener("change", updateLabels);
  $("#orchard-irrigation").addEventListener("change", toggleWater);
  $("#orchard-fit").addEventListener("click", () => { if (state) fit(); });
  $("#orchard-discard").addEventListener("click", () => {
    if (!mayDiscard()) return;
    setDirty(false); drawing = false; picking = null; render(); renderPanel(); status("Unsaved edits discarded.");
  });
  $("#orchard-refresh").addEventListener("click", async () => {
    if (!mayDiscard()) return;
    try {
      const latest = await fetchJson(api);
      state = latest; conflict = false; setDirty(false); drawing = false; picking = null;
      selection = {type: "browse", id: null}; render(); renderPanel(); status("Loaded the latest saved orchard.");
    } catch (error) { status(error.message, true); }
  });
  window.addEventListener("beforeunload", event => { if (dirty || busy) { event.preventDefault(); event.returnValue = ""; } });
  document.addEventListener("keydown", event => { if (event.key === "Escape") { drawing = false; picking = null; status("Map input stopped. Unsaved edits remain in the editor."); } });

  async function init() {
    try {
      if (!window.L) throw new Error("The map library could not load. Check your connection and reload the page.");
      map = L.map("orchard-map", {crs: L.CRS.Simple, minZoom: -5, maxZoom: 8, zoomSnap: .25, doubleClickZoom: false});
      spacesLayer = L.layerGroup().addTo(map); waterLayer = L.layerGroup().addTo(map);
      rowLabels = L.layerGroup().addTo(map); previewLayer = L.layerGroup().addTo(map);
      L.control.scale({imperial: false}).addTo(map);
      map.on("click", event => mapPoint(event.latlng)); map.on("zoomend", updateLabels);
      state = await fetchJson(api);
      render(); renderPanel(); fit();
      status("Select a space to plan a plant, inspect a tree, or edit a row.");
    } catch (error) { status(error.message, true); }
  }
  init();
})();
