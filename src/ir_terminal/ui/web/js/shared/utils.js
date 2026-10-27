(function () {
  'use strict';

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/[&<>'"]/g, function (tag) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag;
    });
  }

  function fmtKB(bytes) {
    if (!bytes && bytes !== 0) return '--';
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + ' MB';
    return (bytes / 1024).toFixed(1) + ' KB';
  }

  function fmtMs(ms) {
    if (!ms || ms <= 0) return '--';
    return Math.round(ms).toString();
  }

  window.SpectraLinkUtils = {
    escapeHtml: escapeHtml,
    fmtKB: fmtKB,
    fmtMs: fmtMs,
  };
})();
