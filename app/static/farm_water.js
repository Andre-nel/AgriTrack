(function () {
  const dialog = document.getElementById("water-asset-modal");
  const configElement = document.getElementById("water-asset-editor-config");
  if (!dialog || !configElement) {
    return;
  }

  let config = {};
  try {
    config = JSON.parse(configElement.textContent || "{}");
  } catch (error) {
    config = {};
  }

  const apiBase = String(dialog.dataset.assetApiBase || "").replace(/\/+$/, "");
  const updateUrlBase = String(dialog.dataset.updateUrlBase || "");
  const form = dialog.querySelector("[data-water-asset-modal-form]");
  const titleElement = dialog.querySelector("[data-water-asset-modal-title]");
  const subtitleElement = dialog.querySelector("[data-water-asset-modal-subtitle]");
  const statusElement = dialog.querySelector("[data-water-asset-modal-status]");
  const importNoteElement = dialog.querySelector("[data-water-asset-import-note]");
  if (!form || !titleElement || !subtitleElement || !statusElement || !importNoteElement || !apiBase) {
    return;
  }

  const assetTypeLabels = config.assetTypeLabels || {};
  const statusOptionsByType = config.statusOptionsByType || {};
  const materialOptionsByType = config.materialOptionsByType || {};
  const waterLevelOptions = Array.isArray(config.waterLevelOptions) ? config.waterLevelOptions : [];
  const windmillSizeOptions = Array.isArray(config.windmillSizeOptions) ? config.windmillSizeOptions : [];
  const weirSizeOptions = Array.isArray(config.weirSizeOptions) ? config.weirSizeOptions : [];
  const troughSizeOptions = Array.isArray(config.troughSizeOptions) ? config.troughSizeOptions : [];
  const waterLevelTypes = new Set(normalizeList(config.waterLevelTypes));
  const capacityTypes = new Set(normalizeList(config.capacityTypes));
  const solarFieldTypes = new Set(normalizeList(config.solarFieldTypes));
  const sourceSystemTypes = new Set(normalizeList(config.sourceSystemTypes));
  const weirFieldTypes = new Set(normalizeList(config.weirFieldTypes));
  const troughFieldTypes = new Set(normalizeList(config.troughFieldTypes));
  const servedPaddockTypes = new Set(normalizeList(config.servedPaddockTypes));
  const locationBoundServedPaddockTypes = new Set(
    normalizeList(config.locationBoundServedPaddockTypes)
  );
  const fieldWrappers = {};
  const fieldControls = {};
  let currentAsset = null;

  Array.from(form.querySelectorAll("[data-water-asset-field]")).forEach((wrapper) => {
    const fieldName = String(wrapper.dataset.waterAssetField || "").trim();
    if (!fieldName) {
      return;
    }
    fieldWrappers[fieldName] = wrapper;
    const control = wrapper.querySelector("input, select, textarea");
    if (control) {
      fieldControls[fieldName] = control;
    }
  });

  const assetTypeSelect = fieldControls.asset_type;
  let activeRequestId = 0;

  function normalizeKey(value) {
    return String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[\s-]+/g, "_");
  }

  function normalizeList(values) {
    if (!Array.isArray(values)) {
      return [];
    }
    return values.map((value) => normalizeKey(value)).filter(Boolean);
  }

  function humanizeChoice(value) {
    return String(value || "")
      .replace(/_/g, " ")
      .replace(/\b\w/g, (match) => match.toUpperCase());
  }

  function setStatus(message, kind) {
    if (!message) {
      statusElement.hidden = true;
      statusElement.textContent = "";
      statusElement.className = "muted water-asset-modal-status";
      return;
    }
    statusElement.hidden = false;
    statusElement.textContent = message;
    statusElement.className =
      kind === "error" ? "error-text water-asset-modal-status" : "muted water-asset-modal-status";
  }

  function clearControlValue(control) {
    if (!control) {
      return;
    }
    if (control.tagName === "SELECT" && control.multiple) {
      Array.from(control.options).forEach((option) => {
        option.selected = false;
      });
      return;
    }
    if (control.type === "checkbox") {
      control.checked = false;
      return;
    }
    control.value = "";
  }

  function setFieldVisibility(fieldName, isVisible) {
    const wrapper = fieldWrappers[fieldName];
    const control = fieldControls[fieldName];
    if (!wrapper || !control) {
      return;
    }
    wrapper.hidden = !isVisible;
    control.disabled = !isVisible;
    if (!isVisible) {
      clearControlValue(control);
    }
  }

  function rebuildSingleSelect(select, values, selectedValue) {
    if (!select) {
      return;
    }

    const desiredValue = selectedValue == null ? "" : String(selectedValue);
    const normalizedValues = Array.isArray(values) ? values : [];
    select.innerHTML = "";

    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "None";
    select.appendChild(placeholder);

    normalizedValues.forEach((value) => {
      const option = document.createElement("option");
      option.value = String(value);
      option.textContent = humanizeChoice(value);
      select.appendChild(option);
    });

    select.value = normalizedValues.some((value) => String(value) === desiredValue) ? desiredValue : "";
  }

  function setMultiSelectValue(select, values) {
    if (!select) {
      return;
    }
    const selectedValues = new Set(
      Array.isArray(values) ? values.map((value) => String(value)) : []
    );
    Array.from(select.options).forEach((option) => {
      option.selected = selectedValues.has(option.value);
    });
  }

  function selectedMultiValues(select) {
    if (!select) {
      return [];
    }
    return Array.from(select.options)
      .filter((option) => option.selected)
      .map((option) => option.value);
  }

  function valueOrBlank(value) {
    return value == null ? "" : String(value);
  }

  function updateModalCopy(assetName, assetType) {
    const normalizedType = normalizeKey(assetType);
    const typeLabel = assetTypeLabels[normalizedType] || humanizeChoice(normalizedType || "water_asset");
    titleElement.textContent = assetName ? "Edit " + assetName : "Edit Asset";
    subtitleElement.textContent =
      typeLabel + " fields and dropdown options update automatically when the asset type changes.";
  }

  function updateImportNote(asset) {
    const placemarkName = String(asset.import_placemark_name || "").trim();
    const styleUrl = String(asset.import_style_url || "").trim();
    if (!placemarkName) {
      importNoteElement.hidden = true;
      importNoteElement.textContent = "";
      return;
    }

    let note = 'Imported from placemark "' + placemarkName + '"';
    if (styleUrl) {
      note += " using " + styleUrl;
    }
    importNoteElement.textContent = note + ".";
    importNoteElement.hidden = true;
  }

  function syncTypeSpecificFields(desiredValues) {
    const assetType = normalizeKey(assetTypeSelect ? assetTypeSelect.value : "");
    const selectedValues = desiredValues || {};
    updateModalCopy(fieldControls.name ? fieldControls.name.value : "", assetType);

    rebuildSingleSelect(
      fieldControls.status,
      statusOptionsByType[assetType] || [],
      selectedValues.status !== undefined ? selectedValues.status : fieldControls.status.value
    );

    const showWaterLevel = waterLevelTypes.has(assetType);
    setFieldVisibility("water_level", showWaterLevel);
    if (showWaterLevel) {
      rebuildSingleSelect(
        fieldControls.water_level,
        waterLevelOptions,
        selectedValues.water_level !== undefined
          ? selectedValues.water_level
          : fieldControls.water_level.value
      );
    }

    setFieldVisibility("capacity_m3", capacityTypes.has(assetType));

    const materialOptions = materialOptionsByType[assetType] || [];
    setFieldVisibility("material", materialOptions.length > 0);
    if (materialOptions.length) {
      rebuildSingleSelect(
        fieldControls.material,
        materialOptions,
        selectedValues.material !== undefined ? selectedValues.material : fieldControls.material.value
      );
    }

    const showWindmillSize = assetType === "windmill";
    setFieldVisibility("windmill_size_ft", showWindmillSize);
    if (showWindmillSize) {
      rebuildSingleSelect(
        fieldControls.windmill_size_ft,
        windmillSizeOptions.map((value) => String(value)),
        selectedValues.windmill_size_ft !== undefined
          ? valueOrBlank(selectedValues.windmill_size_ft)
          : fieldControls.windmill_size_ft.value
      );
    }

    const showWeirSize = weirFieldTypes.has(assetType);
    setFieldVisibility("weir_size", showWeirSize);
    if (showWeirSize) {
      rebuildSingleSelect(
        fieldControls.weir_size,
        weirSizeOptions,
        selectedValues.weir_size !== undefined ? selectedValues.weir_size : fieldControls.weir_size.value
      );
    }

    const showSolarFields = solarFieldTypes.has(assetType);
    setFieldVisibility("solar_brand", showSolarFields);
    setFieldVisibility("solar_kw", showSolarFields);
    setFieldVisibility("solar_head_m", showSolarFields);
    setFieldVisibility("source_system", sourceSystemTypes.has(assetType));

    const showTroughFields = troughFieldTypes.has(assetType);
    setFieldVisibility("trough_size", showTroughFields);
    if (showTroughFields) {
      rebuildSingleSelect(
        fieldControls.trough_size,
        troughSizeOptions,
        selectedValues.trough_size !== undefined ? selectedValues.trough_size : fieldControls.trough_size.value
      );
    }
    const showServedPaddocks = servedPaddockTypes.has(assetType);
    setFieldVisibility("served_paddock_ids", showServedPaddocks);
    if (showServedPaddocks && fieldControls.served_paddock_ids) {
      const locationBoundServedPaddocks = locationBoundServedPaddockTypes.has(assetType);
      if (locationBoundServedPaddocks) {
        const locationPaddockId = valueOrBlank(
          fieldControls.location_paddock_id ? fieldControls.location_paddock_id.value : ""
        );
        setMultiSelectValue(
          fieldControls.served_paddock_ids,
          locationPaddockId ? [locationPaddockId] : []
        );
        fieldControls.served_paddock_ids.disabled = true;
      } else {
        fieldControls.served_paddock_ids.disabled = false;
        setMultiSelectValue(
          fieldControls.served_paddock_ids,
          selectedValues.served_paddock_ids !== undefined
            ? selectedValues.served_paddock_ids
            : selectedMultiValues(fieldControls.served_paddock_ids)
        );
      }
    }
  }

  function setOpenAssetQueryParam(assetId) {
    const url = new URL(window.location.href);
    url.searchParams.set("open_asset_id", assetId);
    window.history.replaceState(null, "", url.toString());
  }

  function clearOpenAssetQueryParam() {
    const url = new URL(window.location.href);
    url.searchParams.delete("open_asset_id");
    window.history.replaceState(null, "", url.toString());
  }

  function currentAssetDraft() {
    if (!currentAsset) {
      return null;
    }
    return {
      ...currentAsset,
      name: fieldControls.name ? fieldControls.name.value : currentAsset.name,
      asset_type: assetTypeSelect ? assetTypeSelect.value : currentAsset.asset_type,
    };
  }

  function emitAssetEvent(name, detail) {
    if (typeof window.CustomEvent !== "function") {
      return;
    }
    dialog.dispatchEvent(new CustomEvent(name, { detail: detail || {} }));
  }

  function resetModalState() {
    currentAsset = null;
    form.reset();
    form.hidden = true;
    form.action = "";
    importNoteElement.hidden = true;
    importNoteElement.textContent = "";
    setStatus("", "");

    Object.keys(fieldWrappers).forEach((fieldName) => {
      const wrapper = fieldWrappers[fieldName];
      const control = fieldControls[fieldName];
      if (!wrapper || !control) {
        return;
      }
      wrapper.hidden = false;
      control.disabled = false;
      if (fieldName === "served_paddock_ids") {
        clearControlValue(control);
      }
    });

    rebuildSingleSelect(fieldControls.status, [], "");
    rebuildSingleSelect(fieldControls.water_level, [], "");
    rebuildSingleSelect(fieldControls.material, [], "");
    rebuildSingleSelect(fieldControls.windmill_size_ft, [], "");
    rebuildSingleSelect(fieldControls.weir_size, [], "");
    rebuildSingleSelect(fieldControls.trough_size, [], "");
    updateModalCopy("", assetTypeSelect ? assetTypeSelect.value : "");
    emitAssetEvent("waterasset:cleared", {});
  }

  function populateForm(asset) {
    resetModalState();
    currentAsset = asset;
    form.action = updateUrlBase + encodeURIComponent(String(asset.id));
    fieldControls.name.value = valueOrBlank(asset.name);
    fieldControls.asset_type.value = valueOrBlank(asset.asset_type);
    fieldControls.latitude.value = valueOrBlank(asset.latitude);
    fieldControls.longitude.value = valueOrBlank(asset.longitude);
    fieldControls.altitude_m.value = valueOrBlank(asset.altitude_m);
    fieldControls.location_paddock_id.value = valueOrBlank(asset.location_paddock_id);
    fieldControls.capacity_m3.value = valueOrBlank(asset.capacity_m3);
    fieldControls.solar_brand.value = valueOrBlank(asset.solar_brand);
    fieldControls.source_system.value = valueOrBlank(asset.source_system);
    fieldControls.solar_kw.value = valueOrBlank(asset.solar_kw);
    fieldControls.solar_head_m.value = valueOrBlank(asset.solar_head_m);
    fieldControls.active.checked = Boolean(asset.active);
    fieldControls.needs_review.checked = Boolean(asset.needs_review);
    setMultiSelectValue(fieldControls.served_paddock_ids, asset.served_paddock_ids || []);
    syncTypeSpecificFields({
      status: asset.status,
      water_level: asset.water_level,
      material: asset.material,
      windmill_size_ft: asset.windmill_size_ft,
      weir_size: asset.weir_size,
      trough_size: asset.trough_size,
      served_paddock_ids: asset.served_paddock_ids || [],
    });
    updateImportNote(asset);
    setStatus("", "");
    form.hidden = false;
    setOpenAssetQueryParam(String(asset.id));

    if (fieldControls.name && typeof fieldControls.name.focus === "function") {
      fieldControls.name.focus();
      if (typeof fieldControls.name.select === "function") {
        fieldControls.name.select();
      }
    }
    emitAssetEvent("waterasset:loaded", { asset: currentAssetDraft() });
  }

  function openDialog() {
    if (dialog.open) {
      return;
    }
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "open");
    }
  }

  function closeDialog() {
    if (typeof dialog.close === "function") {
      dialog.close();
      return;
    }
    dialog.removeAttribute("open");
    resetModalState();
    clearOpenAssetQueryParam();
  }

  function loadAsset(assetId) {
    const normalizedAssetId = String(assetId || "").trim();
    if (!normalizedAssetId) {
      return false;
    }

    activeRequestId += 1;
    const requestId = activeRequestId;
    openDialog();
    resetModalState();
    setStatus("Loading asset...", "info");

    fetch(apiBase + "/" + encodeURIComponent(normalizedAssetId), {
      headers: { Accept: "application/json" },
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error("Unable to load the selected water asset.");
        }
        return response.json();
      })
      .then((asset) => {
        if (requestId !== activeRequestId) {
          return;
        }
        populateForm(asset);
      })
      .catch((error) => {
        if (requestId !== activeRequestId) {
          return;
        }
        resetModalState();
        setStatus(error.message || "Unable to load the selected water asset.", "error");
      });

    return true;
  }

  function handleDialogClosed() {
    activeRequestId += 1;
    resetModalState();
    clearOpenAssetQueryParam();
  }

  if (assetTypeSelect) {
    assetTypeSelect.addEventListener("change", () => {
      syncTypeSpecificFields();
      emitAssetEvent("waterasset:typechange", { asset: currentAssetDraft() });
    });
  }

  if (fieldControls.name) {
    fieldControls.name.addEventListener("input", () => {
      updateModalCopy(fieldControls.name.value, assetTypeSelect ? assetTypeSelect.value : "");
    });
  }

  if (fieldControls.location_paddock_id) {
    fieldControls.location_paddock_id.addEventListener("change", () => {
      syncTypeSpecificFields();
    });
  }

  dialog.querySelectorAll("[data-close-water-asset-modal]").forEach((button) => {
    button.addEventListener("click", closeDialog);
  });

  dialog.addEventListener("click", (event) => {
    const rect = dialog.getBoundingClientRect();
    const clickedBackdrop =
      event.clientX < rect.left ||
      event.clientX > rect.right ||
      event.clientY < rect.top ||
      event.clientY > rect.bottom;
    if (clickedBackdrop) {
      closeDialog();
    }
  });

  if (typeof dialog.addEventListener === "function") {
    dialog.addEventListener("close", handleDialogClosed);
  }

  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-open-water-asset-modal]");
    if (!opener) {
      return;
    }
    const assetId = String(opener.dataset.openWaterAssetModal || "").trim();
    if (!assetId) {
      return;
    }
    if (loadAsset(assetId)) {
      event.preventDefault();
    }
  });

  const initialAssetId = new URLSearchParams(window.location.search).get("open_asset_id");
  if (initialAssetId) {
    loadAsset(initialAssetId);
  }
})();
