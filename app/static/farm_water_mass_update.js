(function () {
  const panel = document.querySelector("[data-water-mass-update-panel]");
  const toggleButton = document.querySelector("[data-water-mass-update-fullscreen-toggle]");

  if (!panel || !toggleButton) {
    return;
  }

  function getFullscreenElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }

  function isPanelFullscreen() {
    return getFullscreenElement() === panel;
  }

  function requestPanelFullscreen() {
    if (typeof panel.requestFullscreen === "function") {
      return panel.requestFullscreen();
    }
    if (typeof panel.webkitRequestFullscreen === "function") {
      return panel.webkitRequestFullscreen();
    }
    return Promise.reject(new Error("Full-screen mode is not supported in this browser."));
  }

  function exitFullscreen() {
    if (typeof document.exitFullscreen === "function") {
      return document.exitFullscreen();
    }
    if (typeof document.webkitExitFullscreen === "function") {
      return document.webkitExitFullscreen();
    }
    return Promise.reject(new Error("Full-screen mode is not supported in this browser."));
  }

  function updateToggleButton() {
    const isFullscreen = isPanelFullscreen();
    toggleButton.textContent = isFullscreen ? "Exit Full Screen" : "Expand Table";
    toggleButton.setAttribute("aria-label", isFullscreen ? "Exit full screen table view" : "Open full screen table view");
    toggleButton.setAttribute("aria-pressed", isFullscreen ? "true" : "false");
    toggleButton.title = isFullscreen ? "Exit full screen" : "Open full screen";
  }

  toggleButton.addEventListener("click", function () {
    const action = isPanelFullscreen() ? exitFullscreen() : requestPanelFullscreen();
    Promise.resolve(action).catch(function (error) {
      window.alert(error.message);
    });
  });

  document.addEventListener("fullscreenchange", updateToggleButton);
  document.addEventListener("webkitfullscreenchange", updateToggleButton);

  updateToggleButton();
})();
