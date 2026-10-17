(function () {
  const data = document.getElementById("farmLsuHistoryData");
  if (!data) return;
  const payload = JSON.parse(data.textContent);
  if (typeof Chart === "undefined") {
    const notice = document.getElementById("farmLsuChartUnavailable");
    if (notice) notice.hidden = false;
    return;
  }
  document.querySelectorAll("[data-farm-lsu-panel]").forEach(function (canvas) {
    const panel = payload.panels[Number(canvas.dataset.farmLsuPanel)];
    new Chart(canvas, {
      type: "line",
      data: {
        labels: payload.labels,
        datasets: panel.datasets.map(function (series) {
          return {
            label: series.label,
            data: series.values,
            borderColor: series.key === "capacity" ? "#267449" : "#245baf",
            borderDash: series.key === "capacity" ? [8, 4] : [],
            borderWidth: 2,
            pointRadius: function (context) {
              const index = context.dataIndex;
              const isolated = (index === 0 || series.values[index - 1] === null) &&
                (index === series.values.length - 1 || series.values[index + 1] === null);
              return isolated && series.values[index] !== null ? 3 : 0;
            },
            pointHoverRadius: 4,
            stepped: "before",
            spanGaps: false,
            fill: false
          };
        })
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { ticks: { maxTicksLimit: 8 }, title: { display: true, text: "Date (UTC)" } },
          y: { min: 0, max: payload.y_max, title: { display: true, text: "LSU" } }
        },
        plugins: {
          legend: { position: "bottom" },
          tooltip: { callbacks: { label: function (context) {
            return context.dataset.label + ": " + Number(context.parsed.y).toFixed(2) + " LSU";
          } } }
        }
      }
    });
  });
})();
