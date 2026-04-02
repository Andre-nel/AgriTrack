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

  const panel = dialog.querySelector("[data-water-asset-connections]");
  const addHost = dialog.querySelector("[data-water-asset-connection-add]");
  const listHost = dialog.querySelector("[data-water-asset-connection-list]");
  const summaryElement = dialog.querySelector("[data-water-asset-connections-summary]");
  const assetForm = dialog.querySelector("[data-water-asset-modal-form]");
  if (!panel || !addHost || !listHost || !summaryElement || !assetForm) {
    return;
  }

  const createConnectionUrl = String(dialog.dataset.createConnectionUrl || "").trim();
  const updateConnectionUrlBase = String(dialog.dataset.updateConnectionUrlBase || "").trim();
  const deleteConnectionUrlBase = String(dialog.dataset.deleteConnectionUrlBase || "").trim();
  if (!createConnectionUrl || !updateConnectionUrlBase || !deleteConnectionUrlBase) {
    return;
  }

  const assetRecords = Array.isArray(config.assetRecords) ? config.assetRecords : [];
  const connectionRecords = Array.isArray(config.connectionRecords) ? config.connectionRecords : [];
  const assetTypeLabels = config.assetTypeLabels || {};
  const flowTypes = Array.isArray(config.flowTypes) ? config.flowTypes.map(normalizeKey) : [];
  const flowTypeLabels = config.flowTypeLabels || {};
  const pumpAssetTypes = new Set(normalizeList(config.pumpAssetTypes));
  const gravityTroughSourceTypes = new Set(normalizeList(config.gravityTroughSourceTypes));
  const transferSourceTypes = new Set(normalizeList(config.transferSourceTypes));
  const transferDestinationTypes = new Set(normalizeList(config.transferDestinationTypes));
  const defaultTroughConnection = config.defaultTroughConnection || {};
  const assetRecordsById = new Map(assetRecords.map((asset) => [String(asset.id), asset]));
  let currentAsset = null;

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

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function assetTypeLabel(assetType) {
    const normalizedType = normalizeKey(assetType);
    return assetTypeLabels[normalizedType] || humanizeChoice(normalizedType || "water_asset");
  }

  function assetLabel(asset) {
    if (!asset) {
      return "Unknown Asset";
    }
    const suffix = asset.active === false ? " [Inactive]" : "";
    return asset.name + " (" + assetTypeLabel(asset.assetType || asset.asset_type) + ")" + suffix;
  }

  function currentAssetDraft() {
    if (!currentAsset) {
      return null;
    }
    const assetTypeControl = assetForm.querySelector("[name='asset_type']");
    const nameControl = assetForm.querySelector("[name='name']");
    return {
      ...currentAsset,
      name: nameControl ? String(nameControl.value || currentAsset.name || "").trim() : currentAsset.name,
      asset_type: assetTypeControl ? assetTypeControl.value : currentAsset.asset_type,
    };
  }

  function rolesForAsset(assetType) {
    const normalizedType = normalizeKey(assetType);
    const roles = [];
    if (gravityTroughSourceTypes.has(normalizedType) || transferSourceTypes.has(normalizedType)) {
      roles.push("source");
    }
    if (normalizedType === "trough" || transferDestinationTypes.has(normalizedType)) {
      roles.push("destination");
    }
    if (pumpAssetTypes.has(normalizedType)) {
      roles.push("pump");
    }
    return roles;
  }

  function allowedFlowTypesForRole(assetType, role) {
    const normalizedType = normalizeKey(assetType);
    const normalizedRole = normalizeKey(role);
    const allowed = [];
    if (normalizedRole === "pump") {
      return pumpAssetTypes.has(normalizedType) ? ["pumped"] : [];
    }
    if (normalizedRole === "source") {
      if (gravityTroughSourceTypes.has(normalizedType) || transferSourceTypes.has(normalizedType)) {
        allowed.push("gravity");
      }
      if (transferSourceTypes.has(normalizedType)) {
        allowed.push("pumped");
      }
      return Array.from(new Set(allowed));
    }
    if (normalizedRole === "destination") {
      if (normalizedType === "trough" || transferDestinationTypes.has(normalizedType)) {
        allowed.push("gravity");
      }
      if (transferDestinationTypes.has(normalizedType)) {
        allowed.push("pumped");
      }
      return Array.from(new Set(allowed));
    }
    return [];
  }

  function allowedDestinationTypes(sourceType, flowType) {
    const normalizedSourceType = normalizeKey(sourceType);
    const normalizedFlowType = normalizeKey(flowType);
    const allowed = [];
    if (normalizedFlowType === "gravity") {
      if (gravityTroughSourceTypes.has(normalizedSourceType)) {
        allowed.push("trough");
      }
      if (transferSourceTypes.has(normalizedSourceType)) {
        transferDestinationTypes.forEach((assetType) => allowed.push(assetType));
      }
    } else if (normalizedFlowType === "pumped" && transferSourceTypes.has(normalizedSourceType)) {
      transferDestinationTypes.forEach((assetType) => allowed.push(assetType));
    }
    return Array.from(new Set(allowed));
  }

  function allowedSourceTypes(destinationType, flowType) {
    const normalizedDestinationType = normalizeKey(destinationType);
    const normalizedFlowType = normalizeKey(flowType);
    if (normalizedFlowType === "gravity") {
      if (normalizedDestinationType === "trough") {
        return Array.from(gravityTroughSourceTypes);
      }
      if (transferDestinationTypes.has(normalizedDestinationType)) {
        return Array.from(transferSourceTypes);
      }
      return [];
    }
    if (normalizedFlowType === "pumped" && transferDestinationTypes.has(normalizedDestinationType)) {
      return Array.from(transferSourceTypes);
    }
    return [];
  }

  function determineRelevantRole(connection, assetId) {
    const normalizedAssetId = String(assetId || "");
    if (String(connection.source_asset_id || "") === normalizedAssetId) {
      return "source";
    }
    if (String(connection.destination_asset_id || "") === normalizedAssetId) {
      return "destination";
    }
    if (String(connection.pump_asset_id || "") === normalizedAssetId) {
      return "pump";
    }
    return "";
  }

  function chooseDefaultRole(assetType) {
    const availableRoles = rolesForAsset(assetType);
    if (availableRoles.includes("destination") && normalizeKey(assetType) === "trough") {
      return "destination";
    }
    if (availableRoles.includes("pump")) {
      return "pump";
    }
    return availableRoles[0] || "";
  }

  function defaultFlowTypeForRole(assetType, role) {
    const allowedFlows = allowedFlowTypesForRole(assetType, role);
    if (normalizeKey(role) === "pump") {
      return "pumped";
    }
    if (normalizeKey(assetType) === "trough" && allowedFlows.includes("gravity")) {
      return "gravity";
    }
    return allowedFlows[0] || "";
  }

  function assetOptionsForTypes(allowedTypes, selectedId, excludedIds) {
    const allowedTypeSet = new Set(normalizeList(allowedTypes));
    const excludedIdSet = new Set((excludedIds || []).map((value) => String(value)));
    const normalizedSelectedId = selectedId == null ? "" : String(selectedId);
    return assetRecords
      .filter((asset) => {
        const assetId = String(asset.id);
        const typeAllowed = allowedTypeSet.has(normalizeKey(asset.assetType));
        const selectedMatch = assetId === normalizedSelectedId;
        const excluded = excludedIdSet.has(assetId) && !selectedMatch;
        if (excluded || (!typeAllowed && !selectedMatch)) {
          return false;
        }
        return asset.active || selectedMatch;
      })
      .sort((left, right) => {
        const leftLabel = left.name.toLowerCase();
        const rightLabel = right.name.toLowerCase();
        if (leftLabel < rightLabel) {
          return -1;
        }
        if (leftLabel > rightLabel) {
          return 1;
        }
        return 0;
      });
  }

  function rebuildChoiceSelect(select, choices, selectedValue, placeholderText) {
    const normalizedSelectedValue = selectedValue == null ? "" : String(selectedValue);
    select.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = placeholderText;
    select.appendChild(placeholder);
    choices.forEach((choice) => {
      const option = document.createElement("option");
      option.value = String(choice.value);
      option.textContent = choice.label;
      select.appendChild(option);
    });
    select.value = choices.some((choice) => String(choice.value) === normalizedSelectedValue)
      ? normalizedSelectedValue
      : "";
  }

  function rebuildAssetSelect(select, assets, selectedValue, placeholderText) {
    rebuildChoiceSelect(
      select,
      assets.map((asset) => ({
        value: asset.id,
        label: assetLabel(asset),
      })),
      selectedValue,
      placeholderText
    );
  }

  function setFixedAssetField(fieldState, asset) {
    fieldState.select.hidden = true;
    fieldState.select.disabled = true;
    fieldState.hidden.disabled = false;
    fieldState.hidden.value = asset ? String(asset.id) : "";
    fieldState.fixed.hidden = false;
    fieldState.fixed.textContent = asset ? assetLabel(asset) : "Not available";
  }

  function setSelectAssetField(fieldState, assets, selectedValue, placeholderText) {
    fieldState.hidden.disabled = true;
    fieldState.hidden.value = "";
    fieldState.fixed.hidden = true;
    fieldState.fixed.textContent = "";
    fieldState.select.hidden = false;
    fieldState.select.disabled = false;
    rebuildAssetSelect(fieldState.select, assets, selectedValue, placeholderText);
  }

  function setWrapperVisible(fieldState, isVisible) {
    fieldState.wrapper.hidden = !isVisible;
    if (!isVisible) {
      fieldState.select.disabled = true;
      fieldState.hidden.disabled = true;
      fieldState.hidden.value = "";
      fieldState.fixed.textContent = "";
    }
  }

  function controlValue(control, fallbackValue) {
    const value = String((control && control.value) || "").trim();
    if (value) {
      return value;
    }
    return fallbackValue == null ? "" : String(fallbackValue);
  }

  function updateSummary(asset, relevantConnections) {
    const connectionCount = relevantConnections.length;
    if (!connectionCount) {
      summaryElement.textContent =
        "No saved links involve this asset yet. Add a relevant source, destination, or pump connection below.";
      return;
    }
    const assetName = String(asset.name || "this asset").trim() || "this asset";
    summaryElement.textContent =
      assetName +
      " is part of " +
      connectionCount +
      " saved connection" +
      (connectionCount === 1 ? "" : "s") +
      ".";
  }

  function connectionCardTitle(connection, role) {
    const sourceAsset = assetRecordsById.get(String(connection.source_asset_id || ""));
    const destinationAsset = assetRecordsById.get(String(connection.destination_asset_id || ""));
    const pumpAsset = assetRecordsById.get(String(connection.pump_asset_id || ""));
    const roleLabel = normalizeKey(role) ? humanizeChoice(role) : "Related";
    const summary =
      (sourceAsset ? sourceAsset.name : connection.source_asset_name || "Unknown") +
      " -> " +
      (destinationAsset ? destinationAsset.name : connection.destination_asset_name || "Unknown");
    const pumpSuffix = pumpAsset ? " | Pump " + pumpAsset.name : "";
    return roleLabel + " | " + summary + pumpSuffix;
  }

  function buildConnectionField(labelText, fieldName) {
    const wrapper = document.createElement("label");
    wrapper.className = "water-asset-connection-field";
    const label = document.createElement("span");
    label.textContent = labelText;
    const select = document.createElement("select");
    select.name = fieldName;
    select.required = true;
    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = fieldName;
    hidden.disabled = true;
    const fixed = document.createElement("div");
    fixed.className = "water-asset-connection-fixed";
    fixed.hidden = true;
    wrapper.append(label, select, hidden, fixed);
    return { wrapper, select, hidden, fixed };
  }

  function buildConnectionEditor(asset, connection) {
    const fixedRole = connection ? determineRelevantRole(connection, asset.id) : "";
    const availableRoles = rolesForAsset(asset.asset_type);
    const card = document.createElement("section");
    card.className = "water-asset-connection-card";

    const heading = document.createElement("h4");
    heading.textContent = connection
      ? connectionCardTitle(connection, fixedRole)
      : "Add Connection";
    card.appendChild(heading);

    const info = document.createElement("p");
    info.className = "muted water-asset-connection-note";
    card.appendChild(info);

    const form = document.createElement("form");
    form.method = "post";
    form.action = connection
      ? updateConnectionUrlBase + encodeURIComponent(String(connection.id))
      : createConnectionUrl;
    form.className = "grid-form water-asset-connection-form";
    card.appendChild(form);

    const openAssetInput = document.createElement("input");
    openAssetInput.type = "hidden";
    openAssetInput.name = "open_asset_id";
    openAssetInput.value = String(asset.id);
    form.appendChild(openAssetInput);

    let roleSelect = null;
    if (!connection) {
      const roleRow = document.createElement("div");
      roleRow.className = "grid-two";
      const roleWrapper = document.createElement("label");
      roleWrapper.className = "water-asset-connection-field";
      const roleLabel = document.createElement("span");
      roleLabel.textContent = "Asset Role";
      roleSelect = document.createElement("select");
      roleSelect.name = "_connection_role";
      roleWrapper.append(roleLabel, roleSelect);
      roleRow.appendChild(roleWrapper);

      const flowWrapper = document.createElement("label");
      flowWrapper.className = "water-asset-connection-field";
      const flowLabel = document.createElement("span");
      flowLabel.textContent = "Flow Type";
      const flowTypeSelect = document.createElement("select");
      flowTypeSelect.name = "flow_type";
      const flowTypeHidden = document.createElement("input");
      flowTypeHidden.type = "hidden";
      flowTypeHidden.name = "_flow_type";
      flowTypeHidden.disabled = true;
      flowWrapper.append(flowLabel, flowTypeSelect, flowTypeHidden);
      roleRow.appendChild(flowWrapper);
      form.appendChild(roleRow);

      availableRoles.forEach((role) => {
        const option = document.createElement("option");
        option.value = role;
        option.textContent = humanizeChoice(role);
        roleSelect.appendChild(option);
      });

      form._flowTypeSelect = flowTypeSelect;
      form._flowTypeHidden = flowTypeHidden;
    } else {
      const flowWrapper = document.createElement("label");
      flowWrapper.className = "water-asset-connection-field";
      const flowLabel = document.createElement("span");
      flowLabel.textContent = "Flow Type";
      const flowTypeSelect = document.createElement("select");
      flowTypeSelect.name = "flow_type";
      const flowTypeHidden = document.createElement("input");
      flowTypeHidden.type = "hidden";
      flowTypeHidden.name = "_flow_type";
      flowTypeHidden.disabled = true;
      flowWrapper.append(flowLabel, flowTypeSelect, flowTypeHidden);
      form.appendChild(flowWrapper);
      form._flowTypeSelect = flowTypeSelect;
      form._flowTypeHidden = flowTypeHidden;
    }

    const sourceField = buildConnectionField("Source Asset", "source_asset_id");
    const destinationField = buildConnectionField("Destination Asset", "destination_asset_id");
    const pumpField = buildConnectionField("Pump Asset", "pump_asset_id");
    pumpField.select.required = false;

    const assetRow = document.createElement("div");
    assetRow.className = "grid-two";
    assetRow.append(sourceField.wrapper, destinationField.wrapper);
    form.appendChild(assetRow);
    form.appendChild(pumpField.wrapper);

    const pipeMaterialInput = document.createElement("input");
    pipeMaterialInput.name = "pipe_material";
    pipeMaterialInput.value = connection ? connection.pipe_material || "" : "";
    const pipeDiameterInput = document.createElement("input");
    pipeDiameterInput.name = "pipe_diameter_spec";
    pipeDiameterInput.value = connection ? connection.pipe_diameter_spec || "" : "";
    const pipeRow = document.createElement("div");
    pipeRow.className = "grid-two";
    const pipeMaterialWrapper = document.createElement("label");
    pipeMaterialWrapper.className = "water-asset-connection-field";
    pipeMaterialWrapper.innerHTML = "<span>Pipe Material</span>";
    pipeMaterialWrapper.appendChild(pipeMaterialInput);
    const pipeDiameterWrapper = document.createElement("label");
    pipeDiameterWrapper.className = "water-asset-connection-field";
    pipeDiameterWrapper.innerHTML = "<span>Pipe Diameter Spec</span>";
    pipeDiameterWrapper.appendChild(pipeDiameterInput);
    pipeRow.append(pipeMaterialWrapper, pipeDiameterWrapper);
    form.appendChild(pipeRow);

    const wallThicknessInput = document.createElement("input");
    wallThicknessInput.name = "pipe_wall_thickness_spec";
    wallThicknessInput.value = connection ? connection.pipe_wall_thickness_spec || "" : "";
    const classInput = document.createElement("input");
    classInput.name = "pipe_class_spec";
    classInput.value = connection ? connection.pipe_class_spec || "" : "";
    const classRow = document.createElement("div");
    classRow.className = "grid-two";
    const wallThicknessWrapper = document.createElement("label");
    wallThicknessWrapper.className = "water-asset-connection-field";
    wallThicknessWrapper.innerHTML = "<span>Wall Thickness Spec</span>";
    wallThicknessWrapper.appendChild(wallThicknessInput);
    const classWrapper = document.createElement("label");
    classWrapper.className = "water-asset-connection-field";
    classWrapper.innerHTML = "<span>Class Spec</span>";
    classWrapper.appendChild(classInput);
    classRow.append(wallThicknessWrapper, classWrapper);
    form.appendChild(classRow);

    const qualityWrapper = document.createElement("label");
    qualityWrapper.className = "water-asset-connection-field";
    qualityWrapper.innerHTML = "<span>Quality Spec</span>";
    const qualityInput = document.createElement("input");
    qualityInput.name = "pipe_quality_spec";
    qualityInput.value = connection ? connection.pipe_quality_spec || "" : "";
    qualityWrapper.appendChild(qualityInput);
    form.appendChild(qualityWrapper);

    const notesWrapper = document.createElement("label");
    notesWrapper.className = "water-asset-connection-field";
    notesWrapper.innerHTML = "<span>Notes</span>";
    const notesInput = document.createElement("textarea");
    notesInput.name = "notes";
    notesInput.rows = 3;
    notesInput.value = connection ? connection.notes || "" : "";
    notesWrapper.appendChild(notesInput);
    form.appendChild(notesWrapper);

    const actions = document.createElement("div");
    actions.className = "row-actions";
    const activeWrapper = document.createElement("label");
    activeWrapper.className = "check-row";
    const activeCheckbox = document.createElement("input");
    activeCheckbox.type = "checkbox";
    activeCheckbox.name = "active";
    activeCheckbox.value = "1";
    activeCheckbox.checked = connection ? Boolean(connection.active) : true;
    activeWrapper.append(activeCheckbox, document.createTextNode(" Active"));
    const submitButton = document.createElement("button");
    submitButton.type = "submit";
    submitButton.className = "btn";
    submitButton.textContent = connection ? "Save Connection" : "Add Connection";
    actions.append(activeWrapper, submitButton);
    form.appendChild(actions);

    if (connection) {
      const deleteForm = document.createElement("form");
      deleteForm.method = "post";
      deleteForm.action =
        deleteConnectionUrlBase + encodeURIComponent(String(connection.id)) + "/delete";
      deleteForm.className = "water-asset-connection-delete-form";
      const deleteAssetInput = document.createElement("input");
      deleteAssetInput.type = "hidden";
      deleteAssetInput.name = "open_asset_id";
      deleteAssetInput.value = String(asset.id);
      const deleteButton = document.createElement("button");
      deleteButton.type = "submit";
      deleteButton.className = "btn btn-secondary";
      deleteButton.textContent = "Delete Connection";
      deleteForm.append(deleteAssetInput, deleteButton);
      card.appendChild(deleteForm);
    }

    const state = {
      asset,
      connection,
      fixedRole,
      availableRoles,
      info,
      form,
      roleSelect,
      flowTypeSelect: form._flowTypeSelect,
      flowTypeHidden: form._flowTypeHidden,
      sourceField,
      destinationField,
      pumpField,
      pipeMaterialInput,
      pipeDiameterInput,
      classInput,
      submitButton,
      activeCheckbox,
    };

    const sync = () => syncConnectionEditor(state);
    if (state.roleSelect) {
      state.roleSelect.addEventListener("change", sync);
    }
    state.flowTypeSelect.addEventListener("change", sync);
    state.sourceField.select.addEventListener("change", sync);
    state.destinationField.select.addEventListener("change", sync);
    state.pumpField.select.addEventListener("change", sync);

    sync();
    return card;
  }

  function syncConnectionEditor(state) {
    const asset = currentAssetDraft() || state.asset;
    const normalizedAssetType = normalizeKey(asset.asset_type);
    const role = normalizeKey(state.fixedRole || (state.roleSelect ? state.roleSelect.value : chooseDefaultRole(asset.asset_type)));
    const allowedFlows = allowedFlowTypesForRole(normalizedAssetType, role);

    if (state.roleSelect) {
      state.roleSelect.value = role;
      state.roleSelect.disabled = state.availableRoles.length <= 1;
    }

    rebuildChoiceSelect(
      state.flowTypeSelect,
      allowedFlows.map((flowType) => ({
        value: flowType,
        label: flowTypeLabels[flowType] || humanizeChoice(flowType),
      })),
      controlValue(
        state.flowTypeSelect,
        state.connection ? state.connection.flow_type : defaultFlowTypeForRole(asset.asset_type, role)
      ),
      "Choose flow type"
    );
    const fixedFlowType = allowedFlows.length <= 1;
    state.flowTypeSelect.disabled = fixedFlowType;
    if (state.flowTypeHidden) {
      state.flowTypeHidden.disabled = !fixedFlowType;
      state.flowTypeHidden.value = state.flowTypeSelect.value;
    }

    const flowType = normalizeKey(state.flowTypeSelect.value);
    const sourceAssetDraft = {
      id: String(asset.id),
      name: asset.name,
      assetType: normalizedAssetType,
      asset_type: normalizedAssetType,
      active: true,
    };

    let selectedSourceId = controlValue(
      state.sourceField.select,
      state.connection ? state.connection.source_asset_id : ""
    );
    let selectedDestinationId = controlValue(
      state.destinationField.select,
      state.connection ? state.connection.destination_asset_id : ""
    );
    let selectedPumpId = controlValue(
      state.pumpField.select,
      state.connection ? state.connection.pump_asset_id : ""
    );

    if (role === "source") {
      setFixedAssetField(state.sourceField, sourceAssetDraft);
      selectedSourceId = String(asset.id);
      const destinationChoices = assetOptionsForTypes(
        allowedDestinationTypes(normalizedAssetType, flowType),
        selectedDestinationId,
        [asset.id]
      );
      setSelectAssetField(
        state.destinationField,
        destinationChoices,
        selectedDestinationId,
        "Choose destination"
      );
      selectedDestinationId = state.destinationField.select.value;
    } else if (role === "destination") {
      setFixedAssetField(state.destinationField, sourceAssetDraft);
      selectedDestinationId = String(asset.id);
      const sourceChoices = assetOptionsForTypes(
        allowedSourceTypes(normalizedAssetType, flowType),
        selectedSourceId,
        [asset.id]
      );
      setSelectAssetField(
        state.sourceField,
        sourceChoices,
        selectedSourceId,
        "Choose source"
      );
      selectedSourceId = state.sourceField.select.value;
    } else if (role === "pump") {
      setFixedAssetField(state.pumpField, sourceAssetDraft);
      selectedPumpId = String(asset.id);
      const sourceChoices = assetOptionsForTypes(
        Array.from(transferSourceTypes),
        selectedSourceId,
        [asset.id]
      );
      setSelectAssetField(state.sourceField, sourceChoices, selectedSourceId, "Choose source");
      selectedSourceId = state.sourceField.select.value;

      const selectedSourceAsset = assetRecordsById.get(String(selectedSourceId));
      const destinationChoices = assetOptionsForTypes(
        selectedSourceAsset
          ? allowedDestinationTypes(selectedSourceAsset.assetType, "pumped")
          : Array.from(transferDestinationTypes),
        selectedDestinationId,
        [asset.id]
      );
      setSelectAssetField(
        state.destinationField,
        destinationChoices,
        selectedDestinationId,
        "Choose destination"
      );
      selectedDestinationId = state.destinationField.select.value;
    } else {
      setWrapperVisible(state.sourceField, false);
      setWrapperVisible(state.destinationField, false);
      setWrapperVisible(state.pumpField, false);
    }

    const showPumpField = flowType === "pumped" && role !== "pump";
    if (role !== "pump") {
      const pumpChoices = assetOptionsForTypes(
        Array.from(pumpAssetTypes),
        selectedPumpId,
        [selectedSourceId, selectedDestinationId]
      );
      if (showPumpField) {
        setSelectAssetField(state.pumpField, pumpChoices, selectedPumpId, "Choose pump");
        selectedPumpId = state.pumpField.select.value;
      } else {
        setWrapperVisible(state.pumpField, false);
      }
    }

    if (role === "source" || role === "destination") {
      setWrapperVisible(state.sourceField, true);
      setWrapperVisible(state.destinationField, true);
      if (flowType === "pumped") {
        setWrapperVisible(state.pumpField, true);
      } else if (role !== "pump") {
        setWrapperVisible(state.pumpField, false);
      }
    } else if (role === "pump") {
      setWrapperVisible(state.sourceField, true);
      setWrapperVisible(state.destinationField, true);
      setWrapperVisible(state.pumpField, true);
    }

    if (!state.connection && role === "destination" && normalizedAssetType === "trough") {
      if (!state.pipeMaterialInput.value) {
        state.pipeMaterialInput.value = String(defaultTroughConnection.pipeMaterial || "");
      }
      if (!state.pipeDiameterInput.value) {
        state.pipeDiameterInput.value = String(defaultTroughConnection.pipeDiameterSpec || "");
      }
      if (!state.classInput.value) {
        state.classInput.value = String(defaultTroughConnection.pipeClassSpec || "");
      }
    }

    const hasRequiredFlow = Boolean(flowType);
    const hasSource = Boolean(
      state.sourceField.hidden.disabled ? state.sourceField.select.value : state.sourceField.hidden.value
    );
    const hasDestination = Boolean(
      state.destinationField.hidden.disabled
        ? state.destinationField.select.value
        : state.destinationField.hidden.value
    );
    const hasPump = role === "pump" || flowType !== "pumped" || Boolean(
      state.pumpField.hidden.disabled ? state.pumpField.select.value : state.pumpField.hidden.value
    );

    state.submitButton.disabled = !(hasRequiredFlow && hasSource && hasDestination && hasPump);
    state.info.textContent =
      role === "pump"
        ? "This asset is fixed as the pump for this connection."
        : "This asset is fixed as the " + humanizeChoice(role || "related") + " asset for this connection.";
  }

  function clearPanel() {
    currentAsset = null;
    panel.hidden = true;
    addHost.innerHTML = "";
    listHost.innerHTML = "";
    summaryElement.textContent =
      "Relevant source, destination, and pump links load here for this asset.";
  }

  function renderConnections(asset) {
    if (!asset || !asset.id) {
      clearPanel();
      return;
    }

    currentAsset = asset;
    panel.hidden = false;
    addHost.innerHTML = "";
    listHost.innerHTML = "";

    const relevantConnections = connectionRecords.filter((connection) => {
      const assetId = String(asset.id);
      return (
        String(connection.source_asset_id || "") === assetId ||
        String(connection.destination_asset_id || "") === assetId ||
        String(connection.pump_asset_id || "") === assetId
      );
    });
    updateSummary(asset, relevantConnections);

    const availableRoles = rolesForAsset(asset.asset_type);
    if (availableRoles.length) {
      addHost.appendChild(buildConnectionEditor(asset, null));
    } else {
      const emptyAddState = document.createElement("p");
      emptyAddState.className = "muted";
      emptyAddState.textContent =
        "This asset type does not participate in editable source, destination, or pump links.";
      addHost.appendChild(emptyAddState);
    }

    if (!relevantConnections.length) {
      const emptyState = document.createElement("p");
      emptyState.className = "muted";
      emptyState.textContent = "No saved connections involve this asset yet.";
      listHost.appendChild(emptyState);
      return;
    }

    relevantConnections.forEach((connection) => {
      listHost.appendChild(buildConnectionEditor(asset, connection));
    });
  }

  dialog.addEventListener("waterasset:loaded", (event) => {
    renderConnections(event.detail ? event.detail.asset : null);
  });

  dialog.addEventListener("waterasset:typechange", (event) => {
    renderConnections(event.detail ? event.detail.asset : currentAssetDraft());
  });

  dialog.addEventListener("waterasset:cleared", clearPanel);
})();
