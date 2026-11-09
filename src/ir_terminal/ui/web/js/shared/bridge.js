(function () {
  'use strict';

  var wiredKeys = {};
  var waitingKeys = {};
  function onReady(key, cb) {
    if (wiredKeys[key] || waitingKeys[key]) return;
    if (window.bridge) {
      waitingKeys[key] = true;
      try {
        cb(window.bridge);
        wiredKeys[key] = true;
      } catch (err) {
        console.error('Bridge wiring failed for ' + key + ':', err);
        setTimeout(function () { onReady(key, cb); }, 300);
      } finally {
        waitingKeys[key] = false;
      }
      return;
    }
    waitingKeys[key] = true;
    setTimeout(function () { waitingKeys[key] = false; onReady(key, cb); }, 300);
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
