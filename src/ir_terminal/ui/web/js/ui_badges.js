(function () {
  'use strict';

  function resolve(target) {
    return typeof target === 'string' ? document.getElementById(target) : target;
  }

  function set(target, opts) {
    var el = resolve(target);
    if (!el) return;
    opts = opts || {};
    if (opts.tone && el.dataset.tone !== opts.tone) el.dataset.tone = opts.tone;
    if (opts.size && el.dataset.size !== opts.size) el.dataset.size = opts.size;
    if (opts.label !== undefined) {
      var label = el.querySelector('.ui-badge__label');
      if (label) {
        if (label.textContent !== opts.label) label.textContent = opts.label;
      } else if (el.textContent !== opts.label) {
        el.textContent = opts.label;
      }
    }
    if (opts.dot !== undefined) {
      var dot = el.querySelector('.ui-badge__dot');
      if (dot) dot.hidden = !opts.dot;
    }
    if (opts.title !== undefined && el.title !== opts.title) el.title = opts.title;
  }

  function setActive(target, active) {
    var el = resolve(target);
    if (!el) return;
    var value = active ? 'true' : 'false';
    if (el.dataset.active !== value) el.dataset.active = value;
  }

  function toneForRtt(rtt) {
    if (!rtt || rtt <= 0) return 'neutral';
    if (rtt < 20) return 'success';
    if (rtt < 60) return 'warning';
    return 'error';
  }

  window.UIBadge = {
    set: set,
    setActive: setActive,
    toneForRtt: toneForRtt
  };
})();
