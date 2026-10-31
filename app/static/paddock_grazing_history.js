(function () {
  const data = document.getElementById("paddockGrazingHistoryData");
  const canvas = document.getElementById("paddockGrazingHistoryChart");
  if (!data || !canvas) return;
  if (typeof Chart === "undefined") {
    canvas.parentElement.hidden = true;
    document.getElementById("paddockGrazingChartUnavailable").hidden = false;
    return;
  }
  const sessions = JSON.parse(data.textContent);
  // Create after expansion so the responsive canvas has a measurable container.
  const card = canvas.closest("details");
  let chart;
  function render() {
    if (!card.open || chart) return;
    chart = new Chart(canvas, {
      type: "bar",
      data: {
        labels: sessions.map((row) => row.start.slice(0, 10) + (row.end ? "" : " (Open)")),
        datasets: [{
          label: "Allocated LSU",
          data: sessions.map((row) => row.allocated_lsu),
          backgroundColor: "#267449",
          maxBarThickness: 48
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { ticks: { maxTicksLimit: 10 }, title: { display: true, text: "Session start (UTC), oldest first" } },
          y: { beginAtZero: true, title: { display: true, text: "Allocated LSU" } }
        },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: {
            title: (items) => sessions[items[0].dataIndex].mob_name,
            label: (context) => "Allocated LSU: " + Number(context.parsed.y).toFixed(2),
            afterLabel: (context) => {
              const row = sessions[context.dataIndex];
              return ["Start (UTC): " + row.start, "End (UTC): " + (row.end || "Open"),
                "Allocation: " + Number(row.allocation_pct).toFixed(2) + "%"];
            }
          } }
        }
      }
    });
  }
  card.addEventListener("toggle", render);
  render();
})();
