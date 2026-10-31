(() => {
  const form = document.getElementById("create-orchard");
  let dirty = false;
  form.addEventListener("input", () => { dirty = true; });
  window.addEventListener("beforeunload", event => {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      const values = new FormData(form);
      const farmId = values.get("farm_id");
      const response = await fetch(`/api/farms/${encodeURIComponent(farmId)}/orchards`, {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: values.get("name")}),
      });
      if (response.redirected) throw new Error("Your session expired. Sign in again before saving.");
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || data.error || "Unable to create orchard.");
      dirty = false;
      window.location.href = `/farms/${encodeURIComponent(farmId)}/orchards/${encodeURIComponent(data.id)}`;
    } catch (error) {
      document.getElementById("create-status").textContent = error.message;
    } finally { button.disabled = false; }
  });
})();
