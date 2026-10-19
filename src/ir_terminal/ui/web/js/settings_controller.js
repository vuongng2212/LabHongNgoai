// Transfer Settings Modal Controller (Apple Modern Precision Light)
//
// Opens via header #btn-top-settings, loads JSON via bridge.getSettings,
// saves the receive directory via bridge.saveSettings, browses via
// bridge.browseTransfersDir and resets to the app default via
// default_transfers_dir. The path field accepts typed/pasted paths (validated
// by the backend). File transfers are unlimited by design (the service streams
// chunks to disk), so there is no max-size field anymore.
(function () {
  // Backend-provided default receive folder (used by "Reset to default").
  let defaultDir = '';

  function $(id) { return document.getElementById(id); }

  function showOverlay() {
    const overlay = $('settings-overlay');
    if (overlay) {
      overlay.classList.remove('hidden');
      overlay.classList.add('flex');
    }
  }

  function hideOverlay() {
    const overlay = $('settings-overlay');
    if (overlay) {
      overlay.classList.add('hidden');
      overlay.classList.remove('flex');
    }
    const err = $('settings-error');
    if (err) { err.classList.add('hidden'); err.innerText = ''; }
  }

  function showError(message) {
    const err = $('settings-error');
    if (err) {
      err.innerText = message;
      err.classList.remove('hidden');
    }
  }

  function toast(type, title, message) {
    if (window.UIFeedback) window.UIFeedback.showToast(type, title, message);
  }

  function applyUnlimitedHint() {
    const hint = $('file-limit-hint');
    if (hint) {
      hint.innerText = 'Drop file to transmit across infrared beam (Unlimited size)';
    }
  }

  function fillForm(config) {
    const dirInput = $('settings-dir');
    if (!config) return;
    if (dirInput && config.transfers_dir) dirInput.value = config.transfers_dir;
    if (config.default_transfers_dir) defaultDir = config.default_transfers_dir;
    applyUnlimitedHint();
  }

  function loadSettings() {
    if (!window.bridge || typeof window.bridge.getSettings !== 'function') {
      toast('warning', 'Backend not connected', 'Settings need the SpectraLink Desktop app.');
      return;
    }
    window.bridge.getSettings((raw) => {
      try {
        fillForm(JSON.parse(raw));
      } catch (e) {
        showError('Could not parse settings from backend.');
      }
    });
  }

  function openSettings() {
    showOverlay();
    loadSettings();
    const dirInput = $('settings-dir');
    if (dirInput) dirInput.focus();
  }

  // Persists a receive directory through the bridge; the backend validates,
  // creates and stores it, and returns an error string when it is unusable.
  function persistDir(dir, successTitle) {
    if (!window.bridge || typeof window.bridge.saveSettings !== 'function') {
      showError('Backend not connected.');
      return;
    }
    const payload = JSON.stringify({ transfers_dir: dir });
    window.bridge.saveSettings(payload, (raw) => {
      let result = null;
      try { result = JSON.parse(raw); } catch (e) { result = null; }
      if (result && result.ok) {
        if (result.default_transfers_dir) defaultDir = result.default_transfers_dir;
        hideOverlay();
        applyUnlimitedHint();
        toast('success', successTitle, 'Unlimited transfer size • ' + (result.transfers_dir || dir));
      } else {
        showError((result && result.error) || 'Failed to save settings.');
      }
    });
  }

  function saveSettings() {
    const dirInput = $('settings-dir');
    const dir = dirInput ? dirInput.value.trim() : '';
    if (!dir) {
      showError('Type or browse a receive directory before saving.');
      return;
    }
    persistDir(dir, 'Settings saved');
  }

  function resetToDefault() {
    const dirInput = $('settings-dir');
    const applyDefault = (dir) => {
      if (!dir) { showError('Could not resolve the default directory.'); return; }
      if (dirInput) dirInput.value = dir;
      persistDir(dir, 'Reset to default');
    };
    if (defaultDir) { applyDefault(defaultDir); return; }
    if (!window.bridge || typeof window.bridge.getSettings !== 'function') {
      showError('Backend not connected.');
      return;
    }
    window.bridge.getSettings((raw) => {
      try {
        const config = JSON.parse(raw);
        if (config && config.default_transfers_dir) defaultDir = config.default_transfers_dir;
        applyDefault(defaultDir);
      } catch (e) {
        showError('Could not resolve the default directory.');
      }
    });
  }

  function browseDirectory() {
    if (!window.bridge || typeof window.bridge.browseTransfersDir !== 'function') {
      toast('warning', 'Backend not connected', 'Directory picker needs the Desktop app.');
      return;
    }
    window.bridge.browseTransfersDir((path) => {
      if (path) {
        const dirInput = $('settings-dir');
        if (dirInput) dirInput.value = path;
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.addEventListener('click', (e) => {
      const target = e.target;
      if (!target || !target.closest) return;
      if (target.closest('#btn-top-settings')) { openSettings(); return; }
      if (target.closest('#settings-close') || target.closest('#settings-cancel')) { hideOverlay(); return; }
      if (target.closest('#settings-backdrop')) { hideOverlay(); return; }
      if (target.closest('#settings-save')) { saveSettings(); return; }
      if (target.closest('#settings-browse')) { browseDirectory(); return; }
      if (target.closest('#settings-reset')) { resetToDefault(); return; }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') hideOverlay();
      if (e.key === 'Enter' && $('settings-overlay') && !$('settings-overlay').classList.contains('hidden')) {
        const active = document.activeElement;
        if (active && active.id === 'settings-dir') saveSettings();
      }
    });
    // Refresh the hint once the bridge is ready (QWebChannel async).
    function refreshHintWhenReady() {
      if (window.bridge && typeof window.bridge.getSettings === 'function') {
        window.bridge.getSettings((raw) => {
          try {
            const config = JSON.parse(raw);
            const dirInput = $('settings-dir');
            if (dirInput && config.transfers_dir) dirInput.value = config.transfers_dir;
            if (config.default_transfers_dir) defaultDir = config.default_transfers_dir;
          } catch (e) { /* keep default hint */ }
          applyUnlimitedHint();
        });
      } else {
        setTimeout(refreshHintWhenReady, 500);
      }
    }
    setTimeout(refreshHintWhenReady, 800);
  });

  window.TransferSettings = {
    open: openSettings,
    close: hideOverlay,
    save: saveSettings,
    reset: resetToDefault,
  };
})();
