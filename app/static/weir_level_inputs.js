(function () {
  function sync(select) {
    const percent = select.form && select.form.elements.namedItem(select.dataset.percentName);
    if (!percent) return;
    percent.readOnly = !select.disabled && select.value === "overflowing";
    if (percent.readOnly) percent.value = "100";
    const status = select.form.elements.namedItem(select.dataset.statusName);
    status.disabled = !select.disabled && select.value === "underflowing";
    if (status.disabled) status.value = "damaged";
  }
  document.addEventListener("change", event => {
    if (event.target.matches("[data-weir-flow]")) sync(event.target);
  });
  document.querySelectorAll("[data-weir-flow]").forEach(sync);
})();
