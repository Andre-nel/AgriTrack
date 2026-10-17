(function () {
  function setupImportIdGenerator() {
    const generator = document.querySelector('[data-import-id-generator]');
    if (!generator) return;

    const generateButton = generator.querySelector('[data-generate-import-id]');
    const copyButton = generator.querySelector('[data-copy-import-id]');
    const input = generator.querySelector('[data-generated-import-id]');
    const feedback = generator.querySelector('[data-import-id-feedback]');

    generateButton.addEventListener('click', async function () {
      generateButton.disabled = true;
      copyButton.disabled = true;
      feedback.textContent = 'Generating import ID…';
      try {
        const response = await fetch(generator.dataset.generateUrl, {
          headers: { Accept: 'application/json' },
          credentials: 'same-origin',
          cache: 'no-store',
        });
        if (!response.ok) throw new Error('Import ID request failed');
        const result = await response.json();
        if (typeof result.import_id !== 'string' || !result.import_id.trim()) {
          throw new Error('No import ID returned');
        }
        input.value = result.import_id;
        feedback.textContent = 'Import ID generated. Copy it into your JSON’s import_id field.';
      } catch (error) {
        feedback.textContent = 'Could not generate an import ID. Please try again.';
      } finally {
        generateButton.disabled = false;
        copyButton.disabled = !input.value;
      }
    });

    copyButton.addEventListener('click', async function () {
      if (!input.value) return;
      feedback.textContent = '';
      try {
        await navigator.clipboard.writeText(input.value);
        feedback.textContent = 'Import ID copied.';
      } catch (error) {
        input.focus();
        input.select();
        input.setSelectionRange(0, input.value.length);
        feedback.textContent = 'Automatic copying is unavailable. The import ID is selected; use your device’s copy command.';
      }
    });
  }

  function setupGroupedForms() {
    document.querySelectorAll('[data-grouped-trade-form]').forEach(function (form) {
      const container = form.querySelector('[data-trade-groups]');
      const pricing = form.querySelector('[data-trade-pricing]');
      const species = form.querySelector('[data-trade-species]');
      const confirm = form.querySelector('[data-confirm-import]');
      function invalidatePreview() {
        if (confirm) confirm.disabled = true;
      }
      function refreshGroups() {
        const groups = container.querySelectorAll('[data-trade-group]');
        form.querySelector('[name="group_count"]').value = groups.length;
        groups.forEach(function (group, index) {
          group.querySelector('[data-group-number]').textContent = index + 1;
          group.querySelectorAll('[name]').forEach(function (input) {
            input.name = input.name.replace(/^group_\d+_/, 'group_' + index + '_');
          });
          group.querySelector('[data-remove-group]').disabled = groups.length === 1;
          group.querySelectorAll('[data-group-pricing]').forEach(function (panel) {
            panel.hidden = panel.dataset.groupPricing !== pricing.value;
            panel.querySelectorAll('input, textarea').forEach(function (input) {
              input.disabled = panel.hidden;
            });
          });
          group.querySelector('[data-group-type]').querySelectorAll('option[data-species]').forEach(function (option) {
            option.hidden = option.dataset.species !== species.value;
          });
          const hair = group.querySelector('[data-goat-hair]');
          hair.hidden = species.value !== 'Goat' || pricing.value !== 'head';
          hair.querySelector('input').disabled = hair.hidden;
          group.querySelector('[data-group-age]').querySelectorAll('option').forEach(function (option) {
            option.hidden = Boolean(option.value) && !(speciesAgeOptions[species.value] || []).includes(option.value);
          });
        });
      }
      form.addEventListener('input', invalidatePreview);
      form.addEventListener('change', function (event) {
        invalidatePreview();
        if (event.target.matches('[data-group-type]')) {
          const selected = event.target.selectedOptions[0];
          if (selected && selected.value) {
            const group = event.target.closest('[data-trade-group]');
            group.querySelector('[data-group-breed]').value = selected.dataset.breed;
            group.querySelector('[data-group-sex]').value = selected.dataset.sex;
            group.querySelector('[data-group-age]').value = selected.dataset.age;
          }
        }
        refreshGroups();
      });
      form.querySelector('[data-add-group]').addEventListener('click', function () {
        container.appendChild(form.querySelector('[data-empty-group]').content.cloneNode(true));
        invalidatePreview();
        refreshGroups();
      });
      container.addEventListener('click', function (event) {
        if (event.target.matches('[data-remove-group]') && container.querySelectorAll('[data-trade-group]').length > 1) {
          event.target.closest('[data-trade-group]').remove();
          invalidatePreview();
          refreshGroups();
        }
      });
      refreshGroups();
    });
  }
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

  setupImportIdGenerator();
  setupTradeForms();
  setupGroupedForms();
  setupCharts();
})();
