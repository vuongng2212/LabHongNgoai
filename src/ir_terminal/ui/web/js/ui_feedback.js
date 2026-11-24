(function () {
  "use strict";

  var TOAST_ICON = {
    success: "check_circle",
    info: "info",
    warning: "warning",
    error: "error",
  };

  var TOAST_STYLE = {
    success: {
      bar: "bg-secondary",
      icon: "text-secondary",
      badge: "bg-secondary/10 text-secondary border-secondary/20",
    },
    info: {
      bar: "bg-primary",
      icon: "text-primary",
      badge: "bg-primary/10 text-primary border-primary/20",
    },
    warning: {
      bar: "bg-tertiary",
      icon: "text-tertiary",
      badge: "bg-tertiary/10 text-tertiary border-tertiary/20",
    },
    error: {
      bar: "bg-error",
      icon: "text-error",
      badge: "bg-error/10 text-error border-error/20",
    },
  };

  var MODAL_STYLE = {
    success: {
      wrap: "bg-emerald-50 text-emerald-600 border border-emerald-200",
      icon: "check_circle",
    },
    error: {
      wrap: "bg-red-50 text-red-600 border border-red-200",
      icon: "error",
    },
    info: {
      wrap: "bg-blue-50 text-blue-600 border border-blue-200",
      icon: "info",
    },
    warning: {
      wrap: "bg-amber-50 text-amber-600 border border-amber-200",
      icon: "warning",
    },
    confirm: {
      wrap: "bg-amber-50 text-amber-600 border border-amber-200",
      icon: "warning",
    },
  };

  function ensureContainers() {
    var toasts = document.getElementById("toast-container");
    var overlay = document.getElementById("modal-overlay");
    return { toasts: toasts, overlay: overlay };
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str).replace(/[&<>'"]/g, function (tag) {
      return (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[
          tag
        ] || tag
      );
    });
  }

  function showToast(type, title, message, opts) {
    var c = ensureContainers();
    if (!c.toasts) return;
    type = TOAST_STYLE[type] ? type : "info";
    var duration =
      opts && opts.durationMs !== undefined ? opts.durationMs : 4200;
    var st = TOAST_STYLE[type];

    var el = document.createElement("div");
    el.className =
      "toast-item pointer-events-auto w-[340px] max-w-[calc(100vw-2rem)] bg-surface-container-lowest rounded-xl border border-black/[0.08] shadow-[0_4px_16px_rgba(0,0,0,0.10)] overflow-hidden flex items-stretch";
    el.innerHTML =
      '<div class="w-1 shrink-0 ' +
      st.bar +
      '"></div>' +
      '<div class="flex items-start gap-2 p-3 flex-1 min-w-0">' +
      '<span class="material-symbols-outlined text-[20px] shrink-0 ' +
      st.icon +
      '">' +
      TOAST_ICON[type] +
      "</span>" +
      '<div class="flex-1 min-w-0">' +
      '<div class="font-title-sm text-title-sm font-semibold text-on-surface leading-tight">' +
      escapeHtml(title) +
      "</div>" +
      (message
        ? '<div class="font-body-md text-body-md text-on-surface-variant mt-0.5 break-words">' +
          escapeHtml(message) +
          "</div>"
        : "") +
      "</div>" +
      '<button class="toast-close shrink-0 p-0.5 rounded hover:bg-surface-container-high text-outline transition-colors" aria-label="Dismiss">' +
      '<span class="material-symbols-outlined text-[16px]">close</span>' +
      "</button>" +
      "</div>";

    var closed = false;
    function close() {
      if (closed) return;
      closed = true;
      el.classList.add("toast-leaving");
      setTimeout(function () {
        el.remove();
      }, 180);
    }
    el.querySelector(".toast-close").addEventListener("click", close);
    c.toasts.appendChild(el);
    while (c.toasts.children.length > 4) c.toasts.firstChild.remove();
    if (duration > 0) setTimeout(close, duration);
    return close;
  }

  function showModal(opts) {
    var c = ensureContainers();
    if (!c.overlay) return function () {};
    opts = opts || {};
    var variant = MODAL_STYLE[opts.variant] ? opts.variant : "info";
    var ms = MODAL_STYLE[variant];
    var isPersistent = Boolean(opts.persistent);

    var iconWrap = document.getElementById("modal-icon-wrap");
    var icon = document.getElementById("modal-icon");
    var title = document.getElementById("modal-title");
    var subtitle = document.getElementById("modal-subtitle");
    var message = document.getElementById("modal-message");
    var details = document.getElementById("modal-details");
    var actionsBox = document.getElementById("modal-actions");
    var closeBtn = document.getElementById("modal-close-btn");

    iconWrap.className =
      "w-10 h-10 rounded-xl flex items-center justify-center shadow-sm " +
      ms.wrap;
    icon.innerText = opts.icon || ms.icon;
    title.innerText = opts.title || "";
    if (subtitle) {
      subtitle.innerText = opts.subtitle || "OPTICAL LINK • STATUS ALERT";
    }
    message.innerText = opts.message || "";
    if (opts.details) {
      details.innerText = opts.details;
      details.classList.remove("hidden");
    } else {
      details.innerText = "";
      details.classList.add("hidden");
    }
    actionsBox.innerHTML = "";

    var closed = false;
    function close(actionId) {
      if (closed) return;
      closed = true;
      c.overlay.classList.add("hidden");
      c.overlay.classList.remove("flex");
      document.removeEventListener("keydown", onKey);
      if (typeof opts.onClose === "function") {
        opts.onClose(actionId);
      }
    }
    function onKey(e) {
      if (e.key === "Escape" && !isPersistent) {
        close("escape");
      }
    }

    if (closeBtn) {
      if (isPersistent) {
        closeBtn.classList.add("hidden");
      } else {
        closeBtn.classList.remove("hidden");
      }
      closeBtn.onclick = function () {
        if (isPersistent) {
          if (typeof opts.onCancel === "function") {
            opts.onCancel(close);
          } else {
            close("close_btn");
          }
        } else {
          close("close_btn");
        }
      };
    }

    var actions =
      opts.actions && opts.actions.length
        ? opts.actions
        : [{ id: "ok", label: "OK", kind: "primary" }];
    actions.forEach(function (a) {
      var b = document.createElement("button");
      if (a.kind === "secondary") {
        b.className = "modal-action-btn-secondary";
      } else {
        b.className = "modal-action-btn-primary";
      }
      b.innerText = a.label;
      b.addEventListener("click", function () {
        if (typeof a.onClick === "function") a.onClick(close);
        else close();
      });
      actionsBox.appendChild(b);
    });

    document.getElementById("modal-backdrop").onclick = function () {
      if (!isPersistent) close("backdrop");
    };
    document.addEventListener("keydown", onKey);
    c.overlay.classList.remove("hidden");
    c.overlay.classList.add("flex");
    var firstBtn = actionsBox.querySelector("button");
    if (firstBtn && variant !== "success") firstBtn.focus();
    return close;
  }

  function confirmModal(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var settled = false;
      function finish(val) {
        if (!settled) {
          settled = true;
          resolve(val);
        }
      }

      showModal({
        variant: "confirm",
        icon: opts.icon || "warning",
        title: opts.title || "Are you sure?",
        message: opts.message || "",
        details: opts.details,
        onClose: function () {
          finish(false);
        },
        actions: [
          {
            id: "cancel",
            label: opts.cancelLabel || "Cancel",
            kind: "secondary",
            onClick: function (close) {
              close();
              finish(false);
            },
          },
          {
            id: "confirm",
            label: opts.confirmLabel || "Confirm",
            kind: opts.danger ? "danger" : "primary",
            onClick: function (close) {
              close();
              finish(true);
            },
          },
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
      btn.classList.add("opacity-70", "pointer-events-none");
      btn.innerHTML =
        '<span class="ui-spinner"></span><span>' +
        escapeHtml(loadingText || "Working...") +
        "</span>";
    } else {
      var prev = BTN_STATE.get(btn);
      if (!prev) return;
      btn.innerHTML = prev.html;
      btn.disabled = prev.disabled;
      btn.classList.remove("opacity-70", "pointer-events-none");
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
