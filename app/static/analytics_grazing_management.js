(function () {
  const payloadEl = document.getElementById("grazingManagementData");
  if (!payloadEl) {
    return;
  }

  let payload = { chart_payload: { labels: [], panels: [] }, timeline_rows: [], initial_period_id: null };
  try {
    payload = JSON.parse(payloadEl.textContent || "{}");
  } catch (error) {
    return;
  }

  const chartPayload = payload.chart_payload || { labels: [], panels: [] };
  const timelineRows = Array.isArray(payload.timeline_rows) ? payload.timeline_rows : [];
  const labels = Array.isArray(chartPayload.labels) ? chartPayload.labels : [];
  const detailPanel = document.getElementById("grazingPeriodDetail");
  const emptyPanel = document.getElementById("grazingPeriodDetailEmpty");
  const periodButtons = Array.from(document.querySelectorAll(".grazing-period-button"));

  function formatValue(value, suffix) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "N/A";
    }
    return Number(value).toFixed(2) + (suffix || "");
  }

  function buildPeriodMap(rows) {
    const map = {};
    rows.forEach((row) => {
      const segments = Array.isArray(row.segments) ? row.segments : [];
      segments.forEach((segment) => {
        map[segment.id] = segment.details || {};
      });
    });
    return map;
  }

  function setDetailField(fieldName, value) {
    if (!detailPanel) {
      return;
    }
    const target = detailPanel.querySelector('[data-field="' + fieldName + '"]');
    if (target) {
      target.textContent = value;
    }
  }

  function updateDetailPanel(periodId, periodMap) {
    const details = periodMap[periodId];
    if (!details || !detailPanel) {
      return;
    }
    setDetailField("paddock_name", details.paddock_name || "N/A");
    setDetailField("state", details.state || "N/A");
    setDetailField("start_date", details.start_date || "N/A");
    setDetailField("end_date", details.end_date || "N/A");
    setDetailField("duration_days", String(details.duration_days || 0) + " day(s)");
    setDetailField("average_lsu", formatValue(details.average_lsu));
    setDetailField("peak_lsu", formatValue(details.peak_lsu));
    setDetailField("average_ha_per_lsu", formatValue(details.average_ha_per_lsu));
    setDetailField("tightest_ha_per_lsu", formatValue(details.tightest_ha_per_lsu));
    setDetailField("pressure_start_pct", formatValue(details.pressure_start_pct, "%"));
    setDetailField("pressure_end_pct", formatValue(details.pressure_end_pct, "%"));
    setDetailField(
      "unique_mobs",
      Array.isArray(details.unique_mobs) && details.unique_mobs.length ? details.unique_mobs.join(", ") : "None"
    );
    setDetailField("change_points_count", String(details.change_points_count || 0));
    detailPanel.dataset.currentPeriodId = periodId;
  }

  function syncSelectedButton(periodId) {
    periodButtons.forEach((button) => {
      const selected = button.dataset.periodId === periodId;
      button.classList.toggle("is-selected", selected);
      button.setAttribute("aria-pressed", selected ? "true" : "false");
    });
  }

  function setupPeriodButtons() {
    const periodMap = buildPeriodMap(timelineRows);
    periodButtons.forEach((button) => {
      button.addEventListener("click", function () {
        const periodId = button.dataset.periodId || "";
        syncSelectedButton(periodId);
        if (emptyPanel) {
          emptyPanel.hidden = true;
        }
        updateDetailPanel(periodId, periodMap);
      });
    });

    if (detailPanel && payload.initial_period_id) {
      syncSelectedButton(payload.initial_period_id);
      updateDetailPanel(payload.initial_period_id, periodMap);
    }
  }

  function colorPalette() {
    return [
      "#1f77b4",
      "#2ca02c",
      "#d62728",
      "#ff7f0e",
      "#17becf",
      "#8c564b",
      "#bcbd22",
      "#e377c2",
      "#9467bd",
      "#7f7f7f",
    ];
  }

  function buildDatasetColors(datasets) {
    const palette = colorPalette();
    const colors = {};
    (datasets || []).forEach((series, index) => {
      colors[series.key] = palette[index % palette.length];
    });
    return colors;
  }

  function renderLineChart(canvas, panel, colorMap) {
    if (!canvas || !window.Chart || !panel) {
      return;
    }

    const datasets = (panel.datasets || []).map((series) => {
      const color = colorMap[series.key] || "#1f77b4";
      return {
        label: series.label,
        data: series.values || [],
        borderColor: color,
        backgroundColor: color,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.2,
        spanGaps: true,
      };
    });

    new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: "index",
          intersect: false,
        },
        plugins: {
          legend: {
            position: "bottom",
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            title: {
              display: true,
              text: panel.y_axis_label || "",
            },
          },
          x: {
            ticks: {
              maxTicksLimit: 12,
            },
          },
        },
      },
    });
  }

  function setupCharts() {
    if (!window.Chart || !Array.isArray(chartPayload.panels) || !chartPayload.panels.length) {
      return;
    }

    if (chartPayload.split_mode === "metric") {
      const colorMap = buildDatasetColors(chartPayload.panels[0].datasets || []);
      chartPayload.panels.forEach((panel, index) => {
        const canvas = document.querySelector('canvas[data-panel-index="' + index + '"]');
        renderLineChart(canvas, panel, colorMap);
      });
      return;
    }

    const metricColorMap = {};
    (chartPayload.panels || []).forEach((group) => {
      (group.metric_panels || []).forEach((panel, panelIndex) => {
        if (!metricColorMap[panel.metric]) {
          metricColorMap[panel.metric] = colorPalette()[panelIndex % colorPalette().length];
        }
      });
    });
    chartPayload.panels.forEach((group, groupIndex) => {
      (group.metric_panels || []).forEach((panel, panelIndex) => {
        const canvas = document.querySelector(
          'canvas[data-group-index="' + groupIndex + '"][data-panel-index="' + panelIndex + '"]'
        );
        renderLineChart(canvas, panel, { ["metric:" + panel.metric]: metricColorMap[panel.metric] });
      });
    });
  }

  setupCharts();
  setupPeriodButtons();
})();
