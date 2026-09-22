/**
 * ModernSelect — progressive enhancement for native <select>.
 * Keeps the original select in the DOM (forms / change listeners still work).
 *
 * Opt out: <select data-native>
 * API: ModernSelect.enhance(el) | refresh(el) | initAll(root)
 */
(function (global) {
  "use strict";

  var CHEVRON =
    '<svg class="ms-chevron" viewBox="0 0 20 20" fill="none" aria-hidden="true">' +
    '<path d="M5.5 7.5L10 12l4.5-4.5" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';

  var CHECK =
    '<svg class="ms-check" viewBox="0 0 16 16" fill="none" aria-hidden="true">' +
    '<path d="M3.5 8.5l3 3 6-7" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';

  var instances = new WeakMap();

  function textOf(opt) {
    return (opt && (opt.label || opt.textContent) || "").trim();
  }

  function closeAll(except) {
    document.querySelectorAll(".ms-wrap.ms-open").forEach(function (wrap) {
      if (except && wrap === except) return;
      wrap.classList.remove("ms-open", "ms-drop-up");
      var inst = instances.get(wrap._msSelect);
      if (inst) inst.activeIndex = -1;
    });
  }

  function placePanel(wrap) {
    wrap.classList.remove("ms-drop-up");
    var rect = wrap.getBoundingClientRect();
    var spaceBelow = window.innerHeight - rect.bottom;
    var spaceAbove = rect.top;
    if (spaceBelow < 280 && spaceAbove > spaceBelow) {
      wrap.classList.add("ms-drop-up");
    }
  }

  function buildOptions(inst) {
    var select = inst.select;
    var list = inst.optionsEl;
    var q = (inst.searchInput && inst.searchInput.value || "").trim().toLowerCase();
    list.innerHTML = "";
    inst.optionButtons = [];

    var opts = Array.prototype.slice.call(select.options || []);
    var shown = 0;

    opts.forEach(function (opt, idx) {
      var label = textOf(opt);
      if (q && label.toLowerCase().indexOf(q) === -1 && String(opt.value).toLowerCase().indexOf(q) === -1) {
        return;
      }
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ms-option";
      btn.setAttribute("role", "option");
      btn.dataset.index = String(idx);
      if (opt.disabled) {
        btn.classList.add("ms-disabled");
        btn.disabled = true;
      }
      if (opt.selected) btn.classList.add("ms-selected");
      btn.innerHTML = '<span>' + escapeHtml(label || (opt.value ? opt.value : "—")) + "</span>" + CHECK;
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (opt.disabled) return;
        select.selectedIndex = idx;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        syncTrigger(inst);
        buildOptions(inst);
        closeAll();
        inst.trigger.focus();
      });
      list.appendChild(btn);
      inst.optionButtons.push(btn);
      shown += 1;
    });

    if (!shown) {
      var empty = document.createElement("div");
      empty.className = "ms-empty";
      empty.textContent = q ? "No matches" : "No options";
      list.appendChild(empty);
    }
  }

  function syncTrigger(inst) {
    var select = inst.select;
    var opt = select.options[select.selectedIndex];
    var label = opt ? textOf(opt) : "";
    var isPlaceholder = !opt || !opt.value;
    inst.labelEl.textContent = label || "Select…";
    inst.labelEl.classList.toggle("is-placeholder", isPlaceholder);
    inst.wrap.classList.toggle("ms-disabled", select.disabled);
    inst.trigger.disabled = !!select.disabled;
  }

  function open(inst) {
    if (inst.select.disabled) return;
    closeAll(inst.wrap);
    if (inst.searchInput) inst.searchInput.value = "";
    buildOptions(inst);
    placePanel(inst.wrap);
    inst.wrap.classList.add("ms-open");
    inst.activeIndex = Math.max(
      0,
      inst.optionButtons.findIndex(function (b) {
        return b.classList.contains("ms-selected");
      })
    );
    highlightActive(inst);
    if (inst.searchWrap.style.display !== "none" && inst.searchInput) {
      setTimeout(function () {
        inst.searchInput.focus();
      }, 0);
    }
  }

  function highlightActive(inst) {
    inst.optionButtons.forEach(function (btn, i) {
      btn.classList.toggle("ms-active", i === inst.activeIndex);
      if (i === inst.activeIndex) {
        btn.scrollIntoView({ block: "nearest" });
      }
    });
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, function (c) {
      return (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] || c
      );
    });
  }

  function enhance(select) {
    if (!select || select.tagName !== "SELECT") return null;
    if (select.hasAttribute("data-native")) return null;
    if (select.dataset.msEnhanced === "1") {
      return instances.get(select) || null;
    }

    var wrap = document.createElement("div");
    wrap.className = "ms-wrap";
    wrap._msSelect = select;

    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add("ms-native");
    select.dataset.msEnhanced = "1";

    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "ms-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    if (select.id) trigger.setAttribute("aria-controls", select.id + "-ms-list");

    var labelEl = document.createElement("span");
    labelEl.className = "ms-label is-placeholder";
    trigger.appendChild(labelEl);
    trigger.insertAdjacentHTML("beforeend", CHEVRON);

    var panel = document.createElement("div");
    panel.className = "ms-panel";
    panel.setAttribute("role", "listbox");
    if (select.id) panel.id = select.id + "-ms-list";

    var searchWrap = document.createElement("div");
    searchWrap.className = "ms-search";
    var searchInput = document.createElement("input");
    searchInput.type = "search";
    searchInput.placeholder = "Search…";
    searchInput.autocomplete = "off";
    searchInput.setAttribute("aria-label", "Filter options");
    searchWrap.appendChild(searchInput);

    var optionsEl = document.createElement("div");
    optionsEl.className = "ms-options";

    panel.appendChild(searchWrap);
    panel.appendChild(optionsEl);
    wrap.appendChild(trigger);
    wrap.appendChild(panel);

    var inst = {
      select: select,
      wrap: wrap,
      trigger: trigger,
      labelEl: labelEl,
      panel: panel,
      searchWrap: searchWrap,
      searchInput: searchInput,
      optionsEl: optionsEl,
      optionButtons: [],
      activeIndex: -1,
      observer: null,
    };
    instances.set(select, inst);

    function updateSearchVisibility() {
      var count = select.options ? select.options.length : 0;
      searchWrap.style.display = count >= 7 ? "block" : "none";
    }

    trigger.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (wrap.classList.contains("ms-open")) {
        closeAll();
      } else {
        open(inst);
      }
      trigger.setAttribute("aria-expanded", wrap.classList.contains("ms-open") ? "true" : "false");
    });

    searchInput.addEventListener("input", function () {
      buildOptions(inst);
      inst.activeIndex = inst.optionButtons.length ? 0 : -1;
      highlightActive(inst);
    });

    searchInput.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === "Escape") {
        handleKey(e, inst);
      }
    });

    trigger.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
        if (!wrap.classList.contains("ms-open")) {
          e.preventDefault();
          open(inst);
          return;
        }
      }
      handleKey(e, inst);
    });

    select.addEventListener("change", function () {
      syncTrigger(inst);
      if (wrap.classList.contains("ms-open")) buildOptions(inst);
    });

    // Rebuild when options are replaced dynamically
    inst.observer = new MutationObserver(function () {
      updateSearchVisibility();
      syncTrigger(inst);
      if (wrap.classList.contains("ms-open")) buildOptions(inst);
    });
    inst.observer.observe(select, { childList: true, subtree: true, attributes: true });

    updateSearchVisibility();
    syncTrigger(inst);
    return inst;
  }

  function handleKey(e, inst) {
    var openNow = inst.wrap.classList.contains("ms-open");
    if (e.key === "Escape") {
      if (openNow) {
        e.preventDefault();
        closeAll();
        inst.trigger.focus();
      }
      return;
    }
    if (!openNow) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!inst.optionButtons.length) return;
      inst.activeIndex = Math.min(inst.optionButtons.length - 1, Math.max(0, inst.activeIndex) + 1);
      highlightActive(inst);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!inst.optionButtons.length) return;
      inst.activeIndex = Math.max(0, (inst.activeIndex < 0 ? 0 : inst.activeIndex) - 1);
      highlightActive(inst);
    } else if (e.key === "Enter") {
      e.preventDefault();
      var btn = inst.optionButtons[inst.activeIndex];
      if (btn) btn.click();
    }
  }

  function refresh(select) {
    var inst = instances.get(select);
    if (!inst) return enhance(select);
    syncTrigger(inst);
    var count = select.options ? select.options.length : 0;
    inst.searchWrap.style.display = count >= 7 ? "block" : "none";
    if (inst.wrap.classList.contains("ms-open")) buildOptions(inst);
    return inst;
  }

  function initAll(root) {
    var scope = root || document;
    scope.querySelectorAll("select:not([data-native])").forEach(enhance);
  }

  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest(".ms-wrap")) return;
    closeAll();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeAll();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initAll();
    });
  } else {
    initAll();
  }

  // Catch selects injected later
  if (typeof MutationObserver !== "undefined") {
    var bodyObserver = new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        m.addedNodes.forEach(function (node) {
          if (node.nodeType !== 1) return;
          if (node.tagName === "SELECT") enhance(node);
          else if (node.querySelectorAll) initAll(node);
        });
      });
    });
    if (document.body) {
      bodyObserver.observe(document.body, { childList: true, subtree: true });
    } else {
      document.addEventListener("DOMContentLoaded", function () {
        bodyObserver.observe(document.body, { childList: true, subtree: true });
      });
    }
  }

  global.ModernSelect = {
    enhance: enhance,
    refresh: refresh,
    initAll: initAll,
  };
})(window);
