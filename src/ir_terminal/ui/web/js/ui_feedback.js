(function () {
  'use strict';

  var TOAST_ICON = {
    success: 'check_circle',
    info: 'info',
    warning: 'warning',
    error: 'error',
  };

  var TOAST_STYLE = {
    success: { bar: 'bg-secondary', icon: 'text-secondary', badge: 'bg-secondary/10 text-secondary border-secondary/20' },
    info: { bar: 'bg-primary', icon: 'text-primary', badge: 'bg-primary/10 text-primary border-primary/20' },
    warning: { bar: 'bg-tertiary', icon: 'text-tertiary', badge: 'bg-tertiary/10 text-tertiary border-tertiary/20' },
    error: { bar: 'bg-error', icon: 'text-error', badge: 'bg-error/10 text-error border-error/20' },
  };

  var MODAL_STYLE = {
    success: { wrap: 'bg-secondary/10 text-secondary', icon: 'check_circle' },
    error: { wrap: 'bg-error/10 text-error', icon: 'error' },
    info: { wrap: 'bg-surface-container text-primary', icon: 'info' },
    confirm: { wrap: 'bg-tertiary/10 text-tertiary', icon: 'warning' },
  };

  function ensureContainers() {
    var toasts = document.getElementById('toast-container');
    var overlay = document.getElementById('modal-overlay');
    return { toasts: toasts, overlay: overlay };
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/[&<>'"]/g, function (tag) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag;
    });
  }

  function showToast(type, title, message, opts) {
    var c = ensureContainers();
    if (!c.toasts) return;
    type = TOAST_STYLE[type] ? type : 'info';
    var duration = opts && opts.durationMs !== undefined ? opts.durationMs : 4200;
    var st = TOAST_STYLE[type];

    var el = document.createElement('div');
    el.className = 'toast-item pointer-events-auto w-[340px] max-w-[calc(100vw-2rem)] bg-surface-container-lowest rounded-xl border border-black/[0.08] shadow-[0_4px_16px_rgba(0,0,0,0.10)] overflow-hidden flex items-stretch';
    el.innerHTML =
      '<div class="w-1 shrink-0 ' + st.bar + '"></div>' +
      '<div class="flex items-start gap-2 p-3 flex-1 min-w-0">' +
        '<span class="material-symbols-outlined text-[20px] shrink-0 ' + st.icon + '">' + TOAST_ICON[type] + '</span>' +
        '<div class="flex-1 min-w-0">' +
          '<div class="font-title-sm text-title-sm font-semibold text-on-surface leading-tight">' + escapeHtml(title) + '</div>' +
          (message ? '<div class="font-body-md text-body-md text-on-surface-variant mt-0.5 break-words">' + escapeHtml(message) + '</div>' : '') +
        '</div>' +
        '<button class="toast-close shrink-0 p-0.5 rounded hover:bg-surface-container-high text-outline transition-colors" aria-label="Dismiss">' +
          '<span class="material-symbols-outlined text-[16px]">close</span>' +
        '</button>' +
      '</div>';

    var closed = false;
    function close() {
      if (closed) return;
      closed = true;
      el.classList.add('toast-leaving');
      setTimeout(function () { el.remove(); }, 180);
    }
    el.querySelector('.toast-close').addEventListener('click', close);
    c.toasts.appendChild(el);
    while (c.toasts.children.length > 4) c.toasts.firstChild.remove();
    if (duration > 0) setTimeout(close, duration);
    return close;
  }

  function showModal(opts) {
    var c = ensureContainers();
    if (!c.overlay) return function () {};
    opts = opts || {};
    var variant = MODAL_STYLE[opts.variant] ? opts.variant : 'info';
    var ms = MODAL_STYLE[variant];

    var iconWrap = document.getElementById('modal-icon-wrap');
    var icon = document.getElementById('modal-icon');
    var title = document.getElementById('modal-title');
    var message = document.getElementById('modal-message');
    var details = document.getElementById('modal-details');
    var actionsBox = document.getElementById('modal-actions');

    iconWrap.className = 'w-12 h-12 rounded-2xl flex items-center justify-center shadow-sm ' + ms.wrap;
    icon.innerText = opts.icon || ms.icon;
    title.innerText = opts.title || '';
    message.innerText = opts.message || '';
    if (opts.details) {
      details.innerText = opts.details;
      details.classList.remove('hidden');
    } else {
      details.innerText = '';
      details.classList.add('hidden');
    }
    actionsBox.innerHTML = '';

    var closed = false;
    function close() {
      if (closed) return;
      closed = true;
      c.overlay.classList.add('hidden');
      c.overlay.classList.remove('flex');
      document.removeEventListener('keydown', onKey);
    }
    function onKey(e) {
      if (e.key === 'Escape' && variant !== 'confirm') close();
    }

    var actions = opts.actions && opts.actions.length ? opts.actions : [{ id: 'ok', label: 'OK', kind: 'primary' }];
    actions.forEach(function (a) {
      var b = document.createElement('button');
      var base = 'h-[32px] px-4 rounded-lg font-title-sm text-title-sm font-semibold active:scale-[0.98] transition-all cursor-pointer ';
      if (a.kind === 'danger') b.className = base + 'bg-error hover:bg-on-error-container text-white shadow-sm';
      else if (a.kind === 'secondary') b.className = base + 'bg-white hover:bg-[#F8FAFC] text-on-surface border border-black/[0.12]';
      else b.className = base + 'bg-primary-container hover:bg-primary text-white shadow-sm';
      b.innerText = a.label;
      b.addEventListener('click', function () {
        if (typeof a.onClick === 'function') a.onClick(close);
        else close();
      });
      actionsBox.appendChild(b);
    });

    document.getElementById('modal-backdrop').onclick = function () {
      if (variant !== 'confirm') close();
    };
    document.addEventListener('keydown', onKey);
    c.overlay.classList.remove('hidden');
    c.overlay.classList.add('flex');
    var firstBtn = actionsBox.querySelector('button');
    if (firstBtn) firstBtn.focus();
    return close;
  }

  function confirmModal(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      showModal({
        variant: 'confirm',
        icon: opts.icon || 'warning',
        title: opts.title || 'Are you sure?',
        message: opts.message || '',
        details: opts.details,
        actions: [
          { id: 'cancel', label: opts.cancelLabel || 'Cancel', kind: 'secondary', onClick: function (close) { close(); resolve(false); } },
          { id: 'confirm', label: opts.confirmLabel || 'Confirm', kind: opts.danger ? 'danger' : 'primary', onClick: function (close) { close(); resolve(true); } },
        ],
      });
    });
  }

  var BTN_STATE = new WeakMap();

  function setButtonLoading(btn, loading, loadingText) {
    if (!btn) return;
    if (loading) {
      if (BTN_STATE.has(btn)) return;
      BTN_STATE.set(btn, { html: btn.innerHTML, disabled: btn.disabled });
      btn.disabled = true;
      btn.classList.add('opacity-70', 'pointer-events-none');
      btn.innerHTML = '<span class="ui-spinner"></span><span>' + escapeHtml(loadingText || 'Working...') + '</span>';
    } else {
      var prev = BTN_STATE.get(btn);
      if (!prev) return;
      btn.innerHTML = prev.html;
      btn.disabled = prev.disabled;
      btn.classList.remove('opacity-70', 'pointer-events-none');
      BTN_STATE.delete(btn);
    }
  }

  window.UIFeedback = {
    showToast: showToast,
    showModal: showModal,
    confirmModal: confirmModal,
    setButtonLoading: setButtonLoading,
  };
})();
