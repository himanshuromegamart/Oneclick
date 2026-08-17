/* ---------------------------------------------------------------------------
   Search as you type.

   Progressive enhancement over a plain GET form: without this file the box
   still works, it just needs the Search button. So a failed script costs
   convenience, not the feature.

   Three things make the difference between this feeling instant and feeling
   broken, and none of them are the fetch itself:

   1. Debounce. One request per keystroke floods the server and the answers
      arrive in a jumble.
   2. Abort the previous request. Responses can arrive out of order, and a
      slow reply to "wat" landing after a fast reply to "water cooler" would
      replace correct results with stale ones. This is the bug people
      describe as "search flickers back to the wrong thing".
   3. Never build rows with innerHTML. Names come from whatever somebody typed
      into a filename; textContent means a document called
      <img onerror=...> is a document called that, not a script.
   --------------------------------------------------------------------------- */

(function () {
  "use strict";

  var DEBOUNCE_MS = 180;   // shorter feels twitchy, longer feels laggy
  var MIN_LENGTH = 2;

  function icon(isContainer) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.7");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");

    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute(
      "d",
      isContainer
        ? "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"
        : "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"
    );
    svg.appendChild(path);
    return svg;
  }

  function build(form) {
    var input = form.querySelector('input[name="q"]');
    var url = form.dataset.suggestUrl;
    if (!input || !url) { return; }

    form.classList.add("search-live");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-autocomplete", "list");
    input.autocomplete = "off";

    var panel = document.createElement("div");
    panel.className = "suggest";
    panel.hidden = true;

    var list = document.createElement("ul");
    list.className = "suggest-list";
    list.setAttribute("role", "listbox");
    panel.appendChild(list);
    form.appendChild(panel);

    var rows = [];        // {url} for each rendered row, in order
    var active = -1;
    var timer = null;
    var inFlight = null;
    var lastTerm = null;

    function close() {
      panel.hidden = true;
      input.setAttribute("aria-expanded", "false");
      active = -1;
    }

    function open() {
      panel.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }

    function highlight(index) {
      var items = list.querySelectorAll(".suggest-row");
      items.forEach(function (item, at) {
        item.classList.toggle("is-active", at === index);
        if (at === index) {
          item.setAttribute("aria-selected", "true");
          if (item.scrollIntoView) { item.scrollIntoView({ block: "nearest" }); }
        } else {
          item.removeAttribute("aria-selected");
        }
      });
      active = index;
    }

    function addRow(result) {
      var item = document.createElement("li");
      item.className = "suggest-row";
      item.setAttribute("role", "option");

      var link = document.createElement("a");
      link.href = result.url;
      if (!result.is_container) {
        link.target = "_blank";
        link.rel = "noopener";
      }

      var art = document.createElement("span");
      art.className = "suggest-icon";
      art.dataset.tone = result.tone;
      art.appendChild(icon(result.is_container));

      var text = document.createElement("span");
      text.className = "suggest-text";

      var name = document.createElement("span");
      name.className = "suggest-name";
      name.textContent = result.name;

      var where = document.createElement("span");
      where.className = "suggest-where";
      // The location is the answer to "which one" - a bare name is not.
      where.textContent = result.location ? "in " + result.location : result.detail;

      text.appendChild(name);
      text.appendChild(where);
      link.appendChild(art);
      link.appendChild(text);
      item.appendChild(link);

      // mousedown, not click: blur fires first on click and would close the
      // panel before the navigation happened.
      item.addEventListener("mousedown", function (event) {
        event.preventDefault();
        window.location.href = result.url;
      });

      list.appendChild(item);
      rows.push({ url: result.url });
    }

    function addFooter(moreUrl, term) {
      var item = document.createElement("li");
      item.className = "suggest-row suggest-more";
      item.setAttribute("role", "option");

      var link = document.createElement("a");
      link.href = moreUrl;
      link.textContent = "See all results for “" + term + "”";
      item.appendChild(link);

      item.addEventListener("mousedown", function (event) {
        event.preventDefault();
        window.location.href = moreUrl;
      });

      list.appendChild(item);
      rows.push({ url: moreUrl });
    }

    function render(payload) {
      list.innerHTML = "";
      rows = [];
      active = -1;

      if (!payload.results.length) {
        var empty = document.createElement("li");
        empty.className = "suggest-empty";
        empty.textContent = "Nothing matches “" + payload.q + "”";
        list.appendChild(empty);
        open();
        return;
      }

      payload.results.forEach(addRow);
      addFooter(payload.more_url, payload.q);
      open();
    }

    function query(term) {
      if (inFlight) { inFlight.abort(); }

      var controller = new AbortController();
      inFlight = controller;

      fetch(url + "?q=" + encodeURIComponent(term), {
        signal: controller.signal,
        headers: { "X-Requested-With": "fetch" },
        credentials: "same-origin",
      })
        .then(function (response) {
          if (!response.ok) { throw new Error("search failed"); }
          return response.json();
        })
        .then(function (payload) {
          // Ignore anything that is no longer what the box says.
          if (payload.q !== input.value.trim()) { return; }
          render(payload);
        })
        .catch(function (error) {
          // An abort is the normal path, not a failure.
          if (error.name === "AbortError") { return; }
          close();
        });
    }

    function schedule() {
      window.clearTimeout(timer);
      var term = input.value.trim();

      if (term.length < MIN_LENGTH) {
        close();
        lastTerm = null;
        return;
      }
      if (term === lastTerm && !panel.hidden) { return; }

      timer = window.setTimeout(function () {
        lastTerm = term;
        query(term);
      }, DEBOUNCE_MS);
    }

    input.addEventListener("input", schedule);
    input.addEventListener("focus", function () {
      if (rows.length && input.value.trim().length >= MIN_LENGTH) { open(); }
    });
    input.addEventListener("blur", function () {
      // Delayed so a mousedown on a row is processed first.
      window.setTimeout(close, 120);
    });

    input.addEventListener("keydown", function (event) {
      if (panel.hidden || !rows.length) {
        return;  // Enter then submits the form, which is the right fallback.
      }

      switch (event.key) {
        case "ArrowDown":
          event.preventDefault();
          highlight((active + 1) % rows.length);
          break;
        case "ArrowUp":
          event.preventDefault();
          highlight((active - 1 + rows.length) % rows.length);
          break;
        case "Enter":
          if (active >= 0) {
            // Only when something is selected. Enter on its own submits to
            // the full results page, which is what people expect.
            event.preventDefault();
            window.location.href = rows[active].url;
          }
          break;
        case "Escape":
          event.preventDefault();
          close();
          break;
      }
    });

    // A click anywhere else dismisses it, the way every other menu behaves.
    document.addEventListener("click", function (event) {
      if (!form.contains(event.target)) { close(); }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form.search[data-suggest-url]").forEach(build);
  });
})();
