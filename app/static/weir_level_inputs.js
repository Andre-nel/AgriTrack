(function () {
  function sync(select) {
    const percent = select.form && select.form.elements.namedItem(select.dataset.percentName);
    if (!percent) return;
    percent.readOnly = select.value === "1";
    if (percent.readOnly) percent.value = "100";
  }
  document.addEventListener("change", event => {
    if (event.target.matches("[data-weir-overflow]")) sync(event.target);
  });
  document.querySelectorAll("[data-weir-overflow]").forEach(sync);
})();
