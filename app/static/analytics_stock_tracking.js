(function () {
  "use strict";

  const canvas = document.getElementById("stockTrackingChart");
  const payloadEl = document.getElementById("stockTrackingChartData");
  if (!canvas || !payloadEl) return;

  const container = canvas.closest(".stock-history-chart") || canvas.parentElement;
  const resetButton = document.getElementById("stockTrackingResetZoom");
  function warn(message) {
    const notice = document.createElement("p");
    notice.className = "stock-history-chart-warning";
    notice.setAttribute("role", "status");
    notice.textContent = message;
    container.before(notice);
  }

  if (!window.Chart) {
    warn("The stock history chart could not load. Latest Totals are available below. Please reload to try again.");
    if (resetButton) resetButton.disabled = true;
    return;
  }

  let payload;
  try {
    payload = JSON.parse(payloadEl.textContent || "{}");
    if (!Array.isArray(payload.datasets) || !Number.isFinite(payload.min_x) || !Number.isFinite(payload.max_x)) {
      throw new Error("Invalid chart data");
    }
  } catch (error) {
    warn("The stock history chart data could not be read. Latest Totals are available below.");
    if (resetButton) resetButton.disabled = true;
    return;
  }

  let hasZoom = false;
  try {
    hasZoom = Boolean(Chart.registry.plugins.get("zoom"));
  } catch (error) {
    // A blocked optional CDN must not prevent the stock history itself from rendering.
  }
  if (!hasZoom) {
    warn("Zoom and pan could not load. You can still inspect notes and filter the chart; reload to retry zoom and pan.");
    if (resetButton) resetButton.disabled = true;
  }

  const colors = ["#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b", "#7a8110", "#b64b8d", "#536878"];
  const missingColor = "#c62828";
  const countFormatter = new Intl.NumberFormat();
  const dateFormatter = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "2-digit", month: "short", year: "numeric" });
  const shortTimeFormatter = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const count = (value) => countFormatter.format(value);
  const signed = (value) => (value > 0 ? "+" : "") + count(value);
  function timestamp(point) {
    const original = point.events && point.events[0] && point.events[0].event_time;
    return (original || new Date(point.x).toISOString()).replace("T", " ").replace(/Z$/, " UTC");
  }
  function node(tag, className, content) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content !== undefined && content !== null) element.textContent = String(content);
    return element;
  }

  let pinned = false;
  let keyboardMode = false;
  let navigating = false;
  let hideTimer;
  let currentItems = [];
  let currentPosition = { x: 0, y: 0 };
  let keyboardIndex = -1;
  let chart;

  const tooltip = node("div", "stock-history-tooltip");
  tooltip.id = "stockTrackingTooltip";
  tooltip.hidden = true;
  tooltip.setAttribute("role", "region");
  tooltip.setAttribute("aria-label", "Stock count change details");
  const tooltipHeader = node("div", "stock-history-tooltip-header");
  const tooltipTitle = node("strong", "", "Stock count details");
  const closeButton = node("button", "stock-history-tooltip-close", "Close");
  closeButton.type = "button";
  closeButton.setAttribute("aria-label", "Close stock count details");
  tooltipHeader.append(tooltipTitle, closeButton);
  const tooltipBody = node("div", "stock-history-tooltip-body");
  const tooltipHint = node("p", "stock-history-tooltip-hint");
  tooltip.append(tooltipHeader, tooltipBody, tooltipHint);
  container.append(tooltip);

  const status = node("p", "stock-history-sr-only");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  container.after(status);
  let keyboardHelp = document.getElementById("stockTrackingKeyboardHelp");
  if (!keyboardHelp) {
    keyboardHelp = node("p", "muted stock-history-keyboard-help", "Keyboard: focus the chart and use Left/Right to inspect events, Enter to pin details, and Escape to close.");
    keyboardHelp.id = "stockTrackingKeyboardHelp";
    container.after(keyboardHelp);
  }
  canvas.tabIndex = 0;
  const describedBy = (canvas.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
  describedBy.push(keyboardHelp.id);
  canvas.setAttribute("aria-describedby", Array.from(new Set(describedBy)).join(" "));

  function hideDetails() {
    window.clearTimeout(hideTimer);
    pinned = false;
    keyboardMode = false;
    currentItems = [];
    tooltip.hidden = true;
    if (chart) {
      chart.setActiveElements([]);
      chart.tooltip.setActiveElements([], { x: 0, y: 0 });
      chart.draw();
    }
  }
  function scheduleHide() {
    window.clearTimeout(hideTimer);
    if (!pinned && !keyboardMode) {
      hideTimer = window.setTimeout(() => {
        if (!tooltip.matches(":hover") && !tooltip.contains(document.activeElement)) tooltip.hidden = true;
      }, 160);
    }
  }
  function placeDetails(position) {
    const canvasRect = canvas.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    const offsetX = canvasRect.left - containerRect.left + position.x;
    const offsetY = canvasRect.top - containerRect.top + position.y;
    const gap = 14;
    const maxLeft = Math.max(0, container.clientWidth - tooltip.offsetWidth);
    let left = offsetX + gap;
    if (left > maxLeft) left = offsetX - tooltip.offsetWidth - gap;
    tooltip.style.left = Math.max(0, Math.min(left, maxLeft)) + "px";
    tooltip.style.top = Math.max(0, Math.min(offsetY - 18, container.clientHeight - tooltip.offsetHeight)) + "px";
  }
  function detailRow(parent, label, value) {
    if (value === undefined || value === null || value === "") return;
    const row = node("p", "stock-history-event-meta");
    row.append(node("strong", "", label + ": "), document.createTextNode(String(value)));
    parent.append(row);
  }
  function showDetails(items, position, announce) {
    if (!items.length || navigating) return;
    window.clearTimeout(hideTimer);
    const pointKey = (selection) => selection.map((item) => item.datasetIndex + ":" + item.index).sort().join("|");
    const scrollTop = tooltip.hidden || pointKey(items) !== pointKey(currentItems) ? 0 : tooltipBody.scrollTop;
    currentItems = items;
    currentPosition = position;
    tooltipBody.replaceChildren();
    const summaries = [];
    items.forEach((item) => {
      const dataset = chart.data.datasets[item.datasetIndex];
      const point = dataset.data[item.index];
      const section = node("section", "stock-history-tooltip-series");
      const title = node("h3", "stock-history-tooltip-series-title", dataset.label);
      title.style.borderColor = dataset.borderColor;
      section.append(title, node("p", "stock-history-event-time", timestamp(point)));
      const summary = point.kind === "event"
        ? count(point.before) + " → " + count(point.y) + " head (" + signed(point.delta) + ")"
        : count(point.y) + " head · " + (point.kind === "opening" ? "Opening count" : "End of selected range");
      section.append(node("p", "stock-history-count-change", summary));
      summaries.push(dataset.label + ": " + summary);
      (point.events || []).forEach((event) => {
        const entry = node("article", "stock-history-tooltip-event");
        const eventLabel = event.event_label || event.event_type || "Stock change";
        entry.append(node("h4", "", eventLabel + (Number.isFinite(event.delta) ? " (" + signed(event.delta) + " head)" : "")));
        detailRow(entry, "Mob", event.mob_name);
        detailRow(entry, "Farm", event.farm_name);
        detailRow(entry, "Animal type", event.animal_type);
        if (event.highlight_missing) {
          (event.highlight_reasons || []).forEach((reason) => entry.append(node("p", "stock-history-highlight-reason", reason)));
        }
        const note = typeof event.note === "string" && event.note.trim() ? event.note : "No note recorded.";
        entry.append(node("p", "stock-history-note", note));
        section.append(entry);
      });
      tooltipBody.append(section);
    });
    tooltipTitle.textContent = pinned ? "Pinned stock count details" : "Stock count details";
    tooltipHint.textContent = pinned ? "Pinned. Close or press Escape to dismiss." : "Click or tap a point to pin these details.";
    tooltip.dataset.pinned = String(pinned);
    tooltip.hidden = false;
    placeDetails(position);
    tooltipBody.scrollTop = scrollTop;
    if (announce) status.textContent = summaries.join(". ") + (pinned ? ". Details pinned." : ". Press Enter to pin details.");
  }

  closeButton.addEventListener("click", () => {
    hideDetails();
    canvas.focus({ preventScroll: true });
  });
  tooltip.addEventListener("mouseenter", () => window.clearTimeout(hideTimer));
  tooltip.addEventListener("mouseleave", scheduleHide);
  tooltip.addEventListener("focusin", () => window.clearTimeout(hideTimer));
  tooltip.addEventListener("focusout", scheduleHide);
  // Do not allow touch-scrolling a long note to pan the plot underneath it.
  ["pointerdown", "touchstart", "touchmove", "wheel"].forEach((type) => {
    tooltip.addEventListener(type, (event) => event.stopPropagation(), { passive: true });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !tooltip.hidden) {
      const returnFocus = tooltip.contains(document.activeElement);
      hideDetails();
      if (returnFocus) canvas.focus({ preventScroll: true });
    }
  });
  canvas.addEventListener("mousemove", () => { if (!pinned) keyboardMode = false; });

  function beginNavigation() {
    navigating = true;
    hideDetails();
    return true;
  }
  function endNavigation() {
    navigating = false;
    keyboardIndex = -1;
  }
  const datasets = payload.datasets.map((series, index) => {
    const color = colors[index % colors.length];
    const isEvent = (context) => context.raw && context.raw.kind === "event";
    const flagged = (context) => isEvent(context) && context.raw.highlight_missing;
    return {
      label: series.label,
      data: series.points || [],
      borderColor: color,
      backgroundColor: color,
      borderWidth: 2,
      stepped: "before",
      tension: 0,
      pointStyle: (context) => flagged(context) ? "rectRot" : "circle",
      pointRadius: (context) => isEvent(context) ? (flagged(context) ? 6 : 3.5) : 0,
      pointHoverRadius: (context) => isEvent(context) ? (flagged(context) ? 8 : 5.5) : 0,
      pointHitRadius: (context) => isEvent(context) ? 9 : 0,
      pointBackgroundColor: (context) => flagged(context) ? missingColor : (isEvent(context) && context.raw.delta === 0 ? "#ffffff" : color),
      pointBorderColor: (context) => flagged(context) ? "#ffffff" : color,
      pointBorderWidth: (context) => flagged(context) ? 2 : 1.5,
    };
  });

  try {
    chart = new Chart(canvas, {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false,
        interaction: { mode: "nearest", axis: "xy", intersect: true },
        onClick(event) {
          if (navigating) return;
          const items = chart.getElementsAtEventForMode(event, "nearest", { intersect: true, axis: "xy" }, false);
          if (!items.length) return;
          pinned = true;
          keyboardMode = false;
          showDetails(items, { x: items[0].element.x, y: items[0].element.y }, true);
        },
        plugins: {
          legend: {
            position: "bottom",
            onClick(event, legendItem, legend) {
              hideDetails();
              keyboardIndex = -1;
              Chart.defaults.plugins.legend.onClick.call(this, event, legendItem, legend);
            },
          },
          tooltip: {
            enabled: false,
            mode: "nearest",
            intersect: true,
            external(context) {
              if (pinned || keyboardMode || navigating) return;
              if (!context.tooltip.opacity) {
                scheduleHide();
                return;
              }
              const items = context.tooltip.dataPoints.map((point) => ({ datasetIndex: point.datasetIndex, index: point.dataIndex }));
              showDetails(items, { x: context.tooltip.caretX, y: context.tooltip.caretY }, false);
            },
          },
          zoom: hasZoom ? {
            limits: { x: { min: payload.min_x, max: payload.max_x, minRange: Math.min(1000, payload.max_x - payload.min_x) } },
            pan: { enabled: true, mode: "x", threshold: 8, onPanStart: beginNavigation, onPanComplete: endNavigation },
            zoom: {
              mode: "x",
              wheel: { enabled: true, modifierKey: "ctrl" },
              pinch: { enabled: true },
              onZoomStart: beginNavigation,
              onZoomComplete: endNavigation,
            },
          } : false,
        },
        scales: {
          x: {
            type: "linear",
            min: payload.min_x,
            max: payload.max_x,
            title: { display: true, text: "Date and time (UTC)" },
            ticks: {
              maxTicksLimit: 8,
              maxRotation: 40,
              callback(value) {
                return (this.max - this.min <= 172800000 ? shortTimeFormatter : dateFormatter).format(new Date(value));
              },
            },
          },
          y: { beginAtZero: true, title: { display: true, text: "Head Count" }, ticks: { precision: 0 } },
        },
      },
    });
  } catch (error) {
    warn("The stock history chart could not be displayed. Latest Totals are available below. Please reload to try again.");
    if (resetButton) resetButton.disabled = true;
    return;
  }

  if (resetButton && hasZoom) {
    resetButton.addEventListener("click", () => {
      hideDetails();
      chart.resetZoom("none");
      endNavigation();
    });
  }
  canvas.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && currentItems.length) {
      event.preventDefault();
      pinned = true;
      showDetails(currentItems, currentPosition, true);
      return;
    }
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const points = [];
    chart.data.datasets.forEach((dataset, datasetIndex) => {
      if (!chart.isDatasetVisible(datasetIndex)) return;
      dataset.data.forEach((point, index) => {
        if (point.kind === "event" && point.x >= chart.scales.x.min && point.x <= chart.scales.x.max) {
          points.push({ datasetIndex, index, x: point.x, y: point.y });
        }
      });
    });
    points.sort((a, b) => a.x - b.x || a.datasetIndex - b.datasetIndex);
    const seen = new Set();
    const unique = points.filter((point) => {
      const key = point.x + ":" + point.y;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    if (!unique.length) {
      status.textContent = "No stock change events in the visible chart range.";
      return;
    }
    if (event.key === "Home") keyboardIndex = 0;
    else if (event.key === "End") keyboardIndex = unique.length - 1;
    else if (keyboardIndex === -1) keyboardIndex = event.key === "ArrowLeft" ? unique.length - 1 : 0;
    else keyboardIndex = Math.max(0, Math.min(unique.length - 1, keyboardIndex + (event.key === "ArrowLeft" ? -1 : 1)));
    const selected = unique[keyboardIndex];
    const items = points.filter((point) => point.x === selected.x && point.y === selected.y);
    keyboardMode = true;
    pinned = false;
    chart.setActiveElements(items);
    chart.update("none");
    const element = chart.getDatasetMeta(selected.datasetIndex).data[selected.index];
    showDetails(items, { x: element.x, y: element.y }, true);
  });
  window.addEventListener("resize", () => { if (!tooltip.hidden) placeDetails(currentPosition); });
})();
