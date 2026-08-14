/* ---------------------------------------------------------------------------
   Searchable dropdown.

   Progressive enhancement: the page ships a real <select>, and this upgrades
   it. If the script fails to load, or JavaScript is off, the native control is
   still there and the form still submits - so the dashboard degrades to plain
   HTML rather than to a broken page.

   No dependency, because a CDN outage should not stop someone adding a
   category.

   Scale note: every option is rendered up front and filtered in memory. That
   is comfortable into the low thousands. Past that this should become a
   server-side lookup - the filtering is the cheap part, holding thousands of
   DOM nodes is not.
   --------------------------------------------------------------------------- */

(function () {
  "use strict";

  function build(select) {
    var options = Array.prototype.map.call(select.options, function (option) {
      return { value: option.value, label: option.text, selected: option.selected };
    });

    var current = options.find(function (o) { return o.selected; }) || options[0];

    // Hide the native control rather than removing it: it stays the thing the
    // form actually submits, so nothing here can desync from what is posted.
    select.hidden = true;
    select.setAttribute("aria-hidden", "true");
    select.tabIndex = -1;

    var root = document.createElement("div");
    root.className = "combo";

    var input = document.createElement("input");
    input.type = "text";
    input.className = "combo-input";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-autocomplete", "list");
    input.autocomplete = "off";
    input.placeholder = "Search categories…";
    input.value = current ? current.label : "";
    if (select.id) {
      input.id = select.id + "_search";
      var label = document.querySelector('label[for="' + select.id + '"]');
      if (label) { label.setAttribute("for", input.id); }
    }

    var list = document.createElement("ul");
    list.className = "combo-list";
    list.setAttribute("role", "listbox");
    list.hidden = true;

    root.appendChild(input);
    root.appendChild(list);
    select.parentNode.insertBefore(root, select.nextSibling);

    var active = -1;
    var shown = [];

    function render(filter) {
      var needle = (filter || "").trim().toLowerCase();
      // Every word must appear somewhere in the path, in any order - so
      // "cooler 40" matches "Products > Water Cooler > 40 Litre".
      var words = needle ? needle.split(/\s+/) : [];

      shown = options.filter(function (option) {
        if (!words.length) { return true; }
        var haystack = option.label.toLowerCase();
        return words.every(function (word) { return haystack.indexOf(word) !== -1; });
      });

      list.innerHTML = "";

      if (!shown.length) {
        var empty = document.createElement("li");
        empty.className = "combo-empty";
        empty.textContent = "No category matches “" + filter + "”";
        list.appendChild(empty);
        return;
      }

      shown.forEach(function (option, index) {
        var item = document.createElement("li");
        item.className = "combo-option";
        item.setAttribute("role", "option");
        item.textContent = option.label;
        item.dataset.value = option.value;
        if (index === active) {
          item.classList.add("is-active");
          item.setAttribute("aria-selected", "true");
        }
        // mousedown, not click: blur fires first on click and would close the
        // list before the selection landed.
        item.addEventListener("mousedown", function (event) {
          event.preventDefault();
          choose(option);
        });
        list.appendChild(item);
      });
    }

    function open() {
      active = -1;
      render(input.value === (current ? current.label : "") ? "" : input.value);
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }

    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      // Snap back to the real selection, so a half-typed search is never
      // mistaken for a choice.
      input.value = current ? current.label : "";
    }

    function choose(option) {
      current = option;
      select.value = option.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      input.value = option.label;
      close();
    }

    function move(step) {
      if (list.hidden) { open(); return; }
      if (!shown.length) { return; }
      active = (active + step + shown.length) % shown.length;
      render(input.value);
      var el = list.children[active];
      if (el && el.scrollIntoView) { el.scrollIntoView({ block: "nearest" }); }
    }

    input.addEventListener("focus", open);
    input.addEventListener("input", function () {
      active = -1;
      render(input.value);
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    });
    input.addEventListener("blur", function () {
      // Delayed so a mousedown on an option is processed first.
      window.setTimeout(close, 120);
    });
    input.addEventListener("keydown", function (event) {
      switch (event.key) {
        case "ArrowDown": event.preventDefault(); move(1); break;
        case "ArrowUp":   event.preventDefault(); move(-1); break;
        case "Enter":
          if (!list.hidden && active >= 0 && shown[active]) {
            event.preventDefault();
            choose(shown[active]);
          }
          break;
        case "Escape":
          if (!list.hidden) { event.preventDefault(); close(); input.blur(); }
          break;
      }
    });

    render("");
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("select[data-searchable]").forEach(build);
  });
})();
