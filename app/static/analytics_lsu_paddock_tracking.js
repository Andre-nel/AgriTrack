(function () {
  const payloadEl = document.getElementById("lsuPaddockTrackingData");
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

  function renderPanel(panel, index) {
    const canvas = document.querySelector('canvas[data-panel-index="' + index + '"]');
    if (!canvas || !panel) {
      return;
    }

    const datasets = (panel.datasets || []).map((series, seriesIndex) => {
      const color = colors[seriesIndex % colors.length];
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

  panels.forEach(renderPanel);
})();
