// File transfer history table (extracted from file_controller.js).
// Owns cache + filter + render + row actions. Depends on:
// window.SpectraLinkUtils.escapeHtml, window.bridge (async), window.UIFeedback (optional).
(function () {
  'use strict';

  function createHistory(opts) {
    const table = opts.table;
    const searchInput = opts.searchInput;
    let cache = [];
    let renderedRows = [];

    function esc(str) {
      if (window.SpectraLinkUtils) return window.SpectraLinkUtils.escapeHtml(str);
      return String(str == null ? '' : str);
    }

    function fmtSize(bytes) {
      if (window.SpectraLinkUtils) return window.SpectraLinkUtils.fmtKB(bytes);
      return (bytes / 1024).toFixed(1) + ' KB';
    }

    function reload() {
      if (!window.bridge || typeof window.bridge.getFileHistory !== 'function') {
        render();
        return;
      }
      window.bridge.getFileHistory((historyJson) => {
        try {
          const transfers = JSON.parse(historyJson);
          cache = Array.isArray(transfers) ? transfers : [];
        } catch (err) {
          console.error('Failed to parse file history JSON:', err);
          cache = [];
        }
        render();
      });
    }

    function pushSuccess(rec) {
      cache.unshift(rec);
      render();
      // Re-sync from hard-drive store so the new row gains its recorded file_path
      // (used by the open-location row action) without changing signals.
      reload();
    }

    function filtered() {
      const query = searchInput ? searchInput.value.trim().toLowerCase() : '';
      return cache.filter(item => {
        if (!query) return true;
        return (item.filename && item.filename.toLowerCase().includes(query)) ||
               (item.crc32_hex && item.crc32_hex.toLowerCase().includes(query)) ||
               (item.status && item.status.toLowerCase().includes(query));
      });
    }

    function render() {
      if (!table) return;
      const query = searchInput ? searchInput.value.trim().toLowerCase() : '';
      table.innerHTML = '';
      renderedRows = [];

      const items = filtered();
      if (items.length === 0) {
        const emptyRow = document.createElement('tr');
        emptyRow.innerHTML = `
          <td colspan="7" class="py-8 text-center text-on-surface-variant text-body-md">
            <div class="flex flex-col items-center justify-center gap-1">
              <span class="material-symbols-outlined text-[24px] text-outline">folder_off</span>
              <span class="font-medium text-on-surface">No optical file transfers recorded yet</span>
              <span class="text-label-sm text-outline">Transferred files will be logged here with verified CRC-32 checksums.</span>
            </div>
          </td>
        `;
        table.appendChild(emptyRow);
        return;
      }

      // Batch all rows into a DocumentFragment: identical DOM, one reflow.
      const fragment = document.createDocumentFragment();
      items.forEach(item => {
        const row = document.createElement('tr');
        row.className = 'hover:bg-surface-container-high/40 transition-colors h-11';

        const sizeStr = fmtSize(item.size_bytes);

        const rowIdx = renderedRows.length;
        renderedRows.push(item);
        row.innerHTML = `
          <td class="px-space-md">
            <div class="flex items-center gap-2">
              <span class="material-symbols-outlined text-[18px] text-primary">description</span>
              <span class="font-mono-data font-semibold text-on-surface truncate max-w-[200px]">${esc(item.filename)}</span>
            </div>
          </td>
          <td class="px-space-md font-mono-data text-on-surface-variant">${sizeStr}</td>
          <td class="px-space-md">
            <span class="text-mono-caption font-mono-caption bg-surface-container px-2 py-0.5 rounded text-on-surface font-semibold">880nm NIR</span>
          </td>
          <td class="px-space-md text-mono-caption font-mono-caption text-on-surface-variant">${esc(item.timestamp || 'Today')}</td>
          <td class="px-space-md">
            <span class="text-mono-caption font-mono-caption font-mono text-outline-variant bg-surface-container-lowest px-1.5 py-0.5 rounded border border-outline-variant/20">CRC32: ${esc(item.crc32_hex)}</span>
          </td>
          <td class="px-space-md">
            <div class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-secondary/10 text-secondary text-mono-caption font-mono-caption font-semibold">
              <span class="w-1.5 h-1.5 rounded-full bg-secondary"></span>
              <span>Verified (${esc(item.status || '100%')})</span>
            </div>
          </td>
          <td class="px-space-md text-right">
            <div class="flex items-center justify-end gap-1">
              <button data-action="open-location" data-idx="${rowIdx}" class="w-7 h-7 flex items-center justify-center rounded hover:bg-surface-container-high text-on-surface-variant hover:text-primary transition-colors" title="Open storage location">
                <span class="material-symbols-outlined text-[16px]">folder_open</span>
              </button>
              <button class="w-7 h-7 flex items-center justify-center rounded hover:bg-surface-container-high text-on-surface-variant transition-colors" title="Verified Checksum">
                <span class="material-symbols-outlined text-[16px] text-secondary">verified</span>
              </button>
            </div>
          </td>
        `;
        fragment.appendChild(row);
      });
      table.appendChild(fragment);
      void query;
    }

    function handleRowAction(actionBtn) {
      if (actionBtn.dataset.action !== 'open-location') return;
      const item = renderedRows[Number(actionBtn.dataset.idx)];
      const path = item && item.file_path;
      if (!window.bridge) {
        if (window.UIFeedback) window.UIFeedback.showToast('warning', 'Backend not connected', 'Storage location needs the SpectraLink Desktop app.');
        return;
      }
      if (!path) {
        if (window.UIFeedback) window.UIFeedback.showToast('info', 'Location not recorded yet', 'History is syncing — try again in a moment.');
        return;
      }
      window.bridge.openTransferLocation(path, (openedDir) => {
        if (!window.UIFeedback) return;
        if (openedDir) {
          window.UIFeedback.showToast('success', 'Storage folder opened', openedDir);
        } else {
          window.UIFeedback.showModal({
            variant: 'info',
            title: 'File no longer there',
            message: 'The recorded file was moved or deleted. Nothing to open.',
            details: path,
          });
        }
      });
    }

    if (searchInput) {
      searchInput.addEventListener('input', render);
    }

    return {
      reload: reload,
      pushSuccess: pushSuccess,
      render: render,
      handleRowAction: handleRowAction,
    };
  }

  window.TransferHistory = {
    create: createHistory,
  };
})();
