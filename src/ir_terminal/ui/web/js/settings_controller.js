// Transfer Settings Modal Controller (Apple Modern Precision Light)
//
// Opens via header #btn-top-settings, loads JSON via bridge.getSettings,
// saves via bridge.saveSettings, browses via bridge.browseTransfersDir.
// Applies the saved max-size hint to #file-limit-hint without reload.
(function () {
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

  function applyLimitHint(maxMb) {
    const hint = $('file-limit-hint');
    if (hint && maxMb > 0) {
      hint.innerText = 'Drop file to transmit across infrared beam (Max ' + maxMb + 'MB)';
    }
  }

  function fillForm(config) {
    const maxInput = $('settings-max-mb');
    const dirInput = $('settings-dir');
    if (!config) return;
    if (maxInput && config.max_file_size_mb !== undefined) {
      maxInput.value = config.max_file_size_mb;
    } else if (maxInput && config.max_file_size_bytes !== undefined) {
      maxInput.value = Math.round(config.max_file_size_bytes / (1024 * 1024));
    }
    if (dirInput && config.transfers_dir) dirInput.value = config.transfers_dir;
    if (config.max_file_size_mb) applyLimitHint(config.max_file_size_mb);
    else if (config.max_file_size_bytes) {
      applyLimitHint(Math.round(config.max_file_size_bytes / (1024 * 1024)));
    }
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
    const maxInput = $('settings-max-mb');
    if (maxInput) maxInput.focus();
  }

  function saveSettings() {
    const maxInput = $('settings-max-mb');
    const dirInput = $('settings-dir');
    const maxMb = maxInput ? parseFloat(maxInput.value) : NaN;
    if (!isFinite(maxMb) || maxMb < 1 || maxMb > 100) {
      showError('Max file size must be a number between 1 and 100 MB.');
      return;
    }
    if (!window.bridge || typeof window.bridge.saveSettings !== 'function') {
      showError('Backend not connected.');
      return;
    }
    const payload = JSON.stringify({
      max_file_size_mb: maxMb,
      transfers_dir: dirInput ? dirInput.value : '',
    });
    window.bridge.saveSettings(payload, (raw) => {
      let result = null;
      try { result = JSON.parse(raw); } catch (e) { result = null; }
      if (result && result.ok) {
        hideOverlay();
        applyLimitHint(result.max_file_size_mb || maxMb);
        toast('success', 'Settings saved', 'Max ' + (result.max_file_size_mb || maxMb) + 'MB • ' + (result.transfers_dir || ''));
      } else {
        showError((result && result.error) || 'Failed to save settings.');
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
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') hideOverlay();
      if (e.key === 'Enter' && $('settings-overlay') && !$('settings-overlay').classList.contains('hidden')) {
        const active = document.activeElement;
        if (active && (active.id === 'settings-max-mb' || active.id === 'settings-dir')) saveSettings();
      }
    });
    // Refresh the limit hint once the bridge is ready (QWebChannel async).
    function refreshHintWhenReady() {
      if (window.bridge && typeof window.bridge.getSettings === 'function') {
        window.bridge.getSettings((raw) => {
          try {
            const config = JSON.parse(raw);
            const mb = config.max_file_size_mb
              || Math.round((config.max_file_size_bytes || 0) / (1024 * 1024));
            if (mb > 0) applyLimitHint(mb);
          } catch (e) { /* keep default hint */ }
        });
      } else {
        setTimeout(refreshHintWhenReady, 500);
      }
    }
    setTimeout(refreshHintWhenReady, 800);
  });

  window.TransferSettings = { open: openSettings, close: hideOverlay, save: saveSettings };
})();
