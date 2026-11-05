(function () {
  const payloadEl = document.getElementById("waterAssetAnalyticsData");
  if (!payloadEl || !window.Chart) {
    return;
  }

  let payload = { labels: [], panels: [] };
  try {
    payload = JSON.parse(payloadEl.textContent || "{}");
  } catch (error) {
    return;
  }

  const labels = Array.isArray(payload.labels) ? payload.labels : [];
  const panels = Array.isArray(payload.panels) ? payload.panels : [];
  const colors = [
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

  function valueLabel(panel, value) {
    if (value === null || value === undefined) {
      return "N/A";
    }
    if (panel.numeric_percentage) return String(value) + "%";
    if (panel.numeric_value) return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 }) + " " + (panel.unit || "");
    const key = String(value);
    return (panel.value_labels || {})[key] || key;
  }

  function renderPanel(panel, index) {
    const canvas = document.querySelector('canvas[data-panel-index="' + index + '"]');
    if (!canvas || !panel) {
      return;
    }
    for (let section = canvas.closest("details"); section; section = section.parentElement.closest("details")) {
      if (!section.open) return;
    }
    const existing = Chart.getChart(canvas);
    if (existing) { existing.resize(); return; }

    const datasets = (panel.datasets || []).map((series, seriesIndex) => {
      const color = colors[seriesIndex % colors.length];
      const flowLabels = (series.overflowing || []).map((overflow, i) =>
        overflow ? "Overflowing" : series.underflowing?.[i] ? "Under flowing" : "");
      return {
        label: series.label,
        data: series.values || [],
        borderColor: color,
        backgroundColor: color,
        borderWidth: 2,
        borderDash: series.reference ? [6, 4] : [],
        flowLabels,
        pointRadius: series.reference ? 0 : flowLabels.map(label => label ? 4 : 0),
        pointStyle: flowLabels.map(label => label === "Overflowing" ? "triangle" : "circle"),
        pointHoverRadius: 4,
        stepped: true,
        spanGaps: false,
      };
    });

    new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets,
      },
      options: {
        animation: false,
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
          tooltip: {
            callbacks: {
              label: function (context) {
                const seriesLabel = context.dataset.label || "";
                const flow = context.dataset.flowLabels[context.dataIndex];
                return seriesLabel + ": " + valueLabel(panel, context.parsed.y) + (flow ? " — " + flow : "");
              },
            },
          },
        },
        scales: {
          y: {
            min: panel.y_min || 0,
            max: panel.y_max == null ? undefined : panel.y_max,
            ticks: {
              stepSize: panel.numeric_percentage ? 25 : panel.numeric_value ? undefined : 1,
              callback: function (value) {
                return valueLabel(panel, value);
              },
            },
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

  panels.forEach(renderPanel);
  document.addEventListener("toggle", event => {
    if (event.target.matches("details.analytics-section") && event.target.open) panels.forEach(renderPanel);
  }, true);
})();
