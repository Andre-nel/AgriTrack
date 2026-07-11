(function () {
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
    if (format === "count") {
      return numberFormat(value, 0, 0);
    }
    return numberFormat(value, 0, 4);
  }

  function setNewTypeState(row, enabled) {
    const fields = row.querySelector("[data-new-type-fields]");
    const stateInput = row.querySelector("[data-use-new-type]");
    const existingSelect = row.querySelector("[data-existing-type-select]");
    const toggle = row.querySelector("[data-new-type-toggle]");
    if (!fields || !stateInput || !toggle) {
      return;
    }

    fields.hidden = !enabled;
    stateInput.value = enabled ? "1" : "";
    toggle.textContent = enabled ? "Use Existing" : "New Type";
    if (existingSelect) {
      existingSelect.disabled = enabled;
      if (enabled) {
        existingSelect.value = "";
      }
    }
    fields.querySelectorAll("input, select, textarea").forEach(function (field) {
      field.disabled = !enabled;
      if (!enabled) {
        field.value = "";
      }
    });
  }

  document.addEventListener("click", function (event) {
    const toggle = event.target.closest("[data-new-type-toggle]");
    if (toggle) {
      const row = toggle.closest("[data-shearing-batch-row]");
      if (row) {
        const stateInput = row.querySelector("[data-use-new-type]");
        setNewTypeState(row, !(stateInput && stateInput.value === "1"));
      }
      return;
    }

    const addButton = event.target.closest("[data-add-batch-row]");
    if (!addButton) {
      return;
    }
    const form = addButton.closest("[data-shearing-batch-form]");
    if (!form) {
      return;
    }
    const template = form.querySelector("[data-batch-row-template]");
    const rowList = form.querySelector("[data-batch-row-list]");
    const rowCountInput = form.querySelector("[data-row-count]");
    if (!template || !rowList || !rowCountInput) {
      return;
    }
    const index = Number(rowCountInput.value || "0");
    const html = template.innerHTML.replace(/__index__/g, String(index));
    const holder = document.createElement("tbody");
    holder.innerHTML = html.trim();
    Array.from(holder.children).forEach(function (row) {
      rowList.appendChild(row);
    });
    rowCountInput.value = String(index + 1);
  });

  function renderPanel(payload, target, panel, index) {
    if (!window.Chart || !panel) {
      return;
    }
    const canvas = document.querySelector(
      'canvas[data-chart-target="' + target + '"][data-panel-index="' + index + '"]'
    );
    if (!canvas) {
      return;
    }

    const labels = Array.isArray(panel.labels) ? panel.labels : payload.labels || [];
    const chartType = panel.chart_type || "bar";
    const datasets = (panel.datasets || []).map(function (series, seriesIndex) {
      const color = colors[seriesIndex % colors.length];
      return {
        label: series.label,
        data: series.values || [],
        borderColor: color,
        backgroundColor: color,
        borderWidth: 2,
      };
    });

    new Chart(canvas, {
      type: chartType,
      data: {
        labels: labels,
        datasets: datasets,
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
              autoSkip: false,
            },
          },
        },
      },
    });
  }

  document.querySelectorAll("[data-shearing-chart-payload]").forEach(function (payloadEl) {
    let payload = { labels: [], panels: [] };
    try {
      payload = JSON.parse(payloadEl.textContent || "{}");
    } catch (error) {
      return;
    }
    const target = payloadEl.dataset.chartTarget || "";
    const panels = Array.isArray(payload.panels) ? payload.panels : [];
    panels.forEach(function (panel, index) {
      renderPanel(payload, target, panel, index);
    });
  });
})();
