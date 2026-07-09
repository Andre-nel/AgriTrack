(function () {
  const payloadEl = document.getElementById("simulatorEquityForecastData");
  const canvas = document.getElementById("simulatorEquityForecastChart");
  if (!payloadEl || !canvas || !window.Chart) {
    return;
  }

  let payload = { labels: [], datasets: [] };
  try {
    payload = JSON.parse(payloadEl.textContent || "{}");
  } catch (error) {
    return;
  }

  function formatMoney(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "N/A";
    }
    return "R " + Number(value).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  const colors = {
    cumulative_profit_loss: "#2f7a5c",
    cumulative_equity: "#1f77b4",
  };

  const datasets = (payload.datasets || []).map((series) => {
    const color = colors[series.key] || "#7f7f7f";
    return {
      seriesKey: series.key,
      label: series.label,
      data: series.values || [],
      borderColor: color,
      backgroundColor: color,
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0.2,
    };
  });

  const chart = new Chart(canvas, {
    type: "line",
    data: {
      labels: payload.labels || [],
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
              return seriesLabel + ": " + formatMoney(context.parsed.y);
            },
          },
        },
      },
      scales: {
        y: {
          ticks: {
            callback: function (value) {
              return formatMoney(value);
            },
          },
          title: {
            display: true,
            text: "Amount",
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

  document.querySelectorAll("[data-equity-series-toggle]").forEach(function (toggle) {
    toggle.addEventListener("change", function () {
      chart.data.datasets.forEach(function (dataset, index) {
        if (dataset.seriesKey === toggle.value) {
          chart.setDatasetVisibility(index, toggle.checked);
        }
      });
      chart.update();
    });
  });
})();
