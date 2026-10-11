// Shared QWebChannel bridge helpers (classic script, file:// safe).
// Depends on: window.UIFeedback (optional), window.bridge (async via QWebChannel).
(function () {
  'use strict';

  // Calls cb once window.bridge is available. Retries every 300ms; wires once per key.
  var wiredKeys = {};
  function onReady(key, cb) {
    if (wiredKeys[key]) return;
    if (window.bridge) {
      wiredKeys[key] = true;
      cb(window.bridge);
      return;
    }
    setTimeout(function () { onReady(key, cb); }, 300);
  }

  // Returns window.bridge, or toasts a warning and returns null when absent
  // (plain-browser preview / bridge still connecting).
  function requireBridge(actionLabel) {
    if (window.bridge) return window.bridge;
    if (window.UIFeedback) {
      window.UIFeedback.showToast(
        'warning',
        'Backend not connected',
        (actionLabel || 'This action') + ' needs the SpectraLink Desktop app link.'
      );
    }
    return null;
  }

  window.SpectraLinkBridge = {
    onReady: onReady,
    requireBridge: requireBridge,
  };
})();
