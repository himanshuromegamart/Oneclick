/* ---------------------------------------------------------------------------
   Opens the change-password dialog without a page load.

   Progressive enhancement. Every trigger is a real link to
   ?set_password=<id>, and the server renders the dialog already open when it
   sees that - so with no JavaScript the feature still works, it just costs a
   round trip. This makes it instant and keeps the URL clean.

   Uses the native <dialog>. showModal() gives focus trapping, Escape to
   close, and inertness of the page behind it, none of which is worth
   rewriting by hand.
   --------------------------------------------------------------------------- */

(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var dialog = document.getElementById("set-password");
    if (!dialog || typeof dialog.showModal !== "function") {
      return;  // Old browser: the plain-link path still works.
    }

    var target = dialog.querySelector("[data-password-target]");
    var label = dialog.querySelector("[data-password-label]");
    var field = dialog.querySelector('input[type="password"]');

    // The server may have rendered it open via the `open` attribute, which is
    // not the same as a modal. Re-open it properly so it gets a backdrop.
    if (dialog.hasAttribute("open")) {
      dialog.close();
      dialog.showModal();
    }

    document.querySelectorAll("[data-password-for]").forEach(function (trigger) {
      trigger.addEventListener("click", function (event) {
        event.preventDefault();

        target.value = trigger.dataset.passwordFor;
        // textContent, not innerHTML: this is somebody's name, and a name is
        // not markup.
        label.textContent = trigger.dataset.passwordName;
        if (field) { field.value = ""; }

        dialog.showModal();
        if (field) { field.focus(); }
      });
    });

    dialog.querySelectorAll("[data-modal-close]").forEach(function (button) {
      button.addEventListener("click", function (event) {
        event.preventDefault();
        dialog.close();
      });
    });

    // Clicking the backdrop closes it, the way every other dialog behaves.
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) { dialog.close(); }
    });
  });
})();
