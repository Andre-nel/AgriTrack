(function () {
  const payloadEl = document.getElementById("shearingAnalyticsData");
  if (!payloadEl || !window.Chart) {
    return;
  }

  let payload = { labels: [], panels: [] };
  try {
    payload = JSON.parse(payloadEl.textContent || "{}");
  } catch (error) {
    return;
  }

  const defaultLabels = Array.isArray(payload.labels) ? payload.labels : [];
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

  function numberFormat(value, minimumFractionDigits, maximumFractionDigits) {
    return Number(value).toLocaleString(undefined, {
      minimumFractionDigits,
      maximumFractionDigits,
    });
  }

  function formatValue(format, value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "N/A";
    }
    if (format === "money") {
      return "R " + numberFormat(value, 2, 2);
    }
    if (format === "price") {
      return "R " + numberFormat(value, 4, 4) + "/kg";
    }
    if (format === "kg") {
      return numberFormat(value, 3, 3) + " kg";
    }
    return numberFormat(value, 0, 4);
  }

  function renderPanel(panel, index) {
    const canvas = document.querySelector('canvas[data-panel-index="' + index + '"]');
    if (!canvas || !panel) {
      return;
    }

    const chartType = panel.chart_type || "line";
    const panelLabels = Array.isArray(panel.labels) ? panel.labels : defaultLabels;
    const datasets = (panel.datasets || []).map((series, seriesIndex) => {
      const color = colors[seriesIndex % colors.length];
      const dataset = {
        label: series.label,
        data: series.values || [],
        borderColor: color,
        backgroundColor: color,
        borderWidth: 2,
      };
      if (chartType === "line") {
        dataset.pointRadius = 0;
        dataset.pointHoverRadius = 4;
        dataset.tension = 0.2;
        dataset.spanGaps = true;
      }
      return dataset;
    });

    new Chart(canvas, {
      type: chartType,
      data: {
        labels: panelLabels,
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
          tooltip: {
            callbacks: {
              label: function (context) {
                const seriesLabel = context.dataset.label || "";
                return seriesLabel + ": " + formatValue(panel.value_format, context.parsed.y);
              },
            },
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            ticks: {
              callback: function (value) {
                return formatValue(panel.value_format, value);
              },
            },
            title: {
              display: true,
              text: panel.y_axis_label || "",
            },
          },
          x: {
            ticks: {
              maxRotation: 45,
              maxTicksLimit: 12,
            },
          },
        },
      },
    });
  }

  panels.forEach(renderPanel);
})();
