(function () {
  'use strict';

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
