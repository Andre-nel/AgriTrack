(function () {
  const modeSpecies = {
    lamb: "Sheep",
    calf: "Cattle",
    goat: "Goat",
  };
  const defaultAgeClass = {
    lamb: "lamb",
    calf: "calf",
    goat: "kid",
  };
  const speciesSexOptions = {
    Sheep: ["mixed", "ewe", "ram", "wether"],
    Cattle: ["mixed", "cow", "bul", "ox"],
    Goat: ["mixed", "ewe", "ram", "wether"],
  };
  const speciesAgeOptions = {
    Sheep: ["lamb", "young", "adult", "old"],
    Cattle: ["calf", "young", "adult", "old"],
    Goat: ["kid", "young", "adult", "old"],
  };

  function setSelectOptions(select, allowedValues, fallbackValue) {
    if (!select) {
      return;
    }
    let hasSelectedVisibleOption = false;
    Array.from(select.options).forEach(function (option) {
      const visible = !option.value || allowedValues.indexOf(option.value) !== -1;
      option.hidden = !visible;
      if (visible && option.selected && option.value) {
        hasSelectedVisibleOption = true;
      }
    });
    if (!hasSelectedVisibleOption) {
      select.value = fallbackValue;
    }
  }

  function setupTradeForms() {
    document.querySelectorAll("[data-livestock-trade-form]").forEach(function (form) {
      const modeSelect = form.querySelector("[data-livestock-mode]");
      const animalTypeSelect = form.querySelector("[data-animal-type-select]");
      const sexSelect = form.querySelector('select[name="sex"]');
      const ageClassSelect = form.querySelector('select[name="age_class"]');
      if (!modeSelect) {
        return;
      }

      function updateMode() {
        const mode = modeSelect.value || "lamb";
        const species = modeSpecies[mode] || "Sheep";
        const pricingModel = mode === "goat" ? "head" : "weight";

        form.querySelectorAll("[data-pricing-panel]").forEach(function (panel) {
          panel.hidden = panel.dataset.pricingPanel !== pricingModel;
        });

        if (animalTypeSelect) {
          let selectedIsVisible = !animalTypeSelect.value;
          Array.from(animalTypeSelect.options).forEach(function (option) {
            if (!option.value) {
              option.hidden = false;
              return;
            }
            const visible = option.dataset.species === species;
            option.hidden = !visible;
            if (visible && option.selected) {
              selectedIsVisible = true;
            }
          });
          if (!selectedIsVisible) {
            animalTypeSelect.value = "";
          }
        }

        setSelectOptions(sexSelect, speciesSexOptions[species] || [], "mixed");
        setSelectOptions(ageClassSelect, speciesAgeOptions[species] || [], defaultAgeClass[mode]);
      }

      modeSelect.addEventListener("change", updateMode);
      updateMode();
    });
  }

  function numberFormat(value, minimumFractionDigits, maximumFractionDigits) {
    return Number(value).toLocaleString(undefined, {
      minimumFractionDigits: minimumFractionDigits,
      maximumFractionDigits: maximumFractionDigits,
    });
  }

  function formatValue(format, value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "N/A";
    }
    if (format === "money") {
      return "R " + numberFormat(value, 2, 4);
    }
    if (format === "kg") {
      return numberFormat(value, 3, 3) + " kg";
    }
    if (format === "count") {
      return numberFormat(value, 0, 0);
    }
    return numberFormat(value, 0, 4);
  }

  function setupCharts() {
    const payloadEl = document.getElementById("livestockTradeChartData");
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
    ];

    panels.forEach(function (panel, index) {
      const canvas = document.querySelector('canvas[data-panel-index="' + index + '"]');
      if (!canvas) {
        return;
      }
      const datasets = (panel.datasets || []).map(function (series, seriesIndex) {
        const color = colors[seriesIndex % colors.length];
        return {
          label: series.label,
          data: series.values || [],
          borderColor: color,
          backgroundColor: color,
          borderWidth: 2,
          pointRadius: 3,
          pointHoverRadius: 5,
          tension: 0.2,
          spanGaps: true,
        };
      });

      new Chart(canvas, {
        type: "line",
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
                maxTicksLimit: 12,
              },
            },
          },
        },
      });
    });
  }

  setupTradeForms();
  setupCharts();
})();
