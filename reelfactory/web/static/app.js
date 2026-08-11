/* ===========================================================================
   Reel Factory — the two bits of interaction the UI needs.

   1. wizard()  turns a long <form> into one-question-per-screen steps.
   2. tabs()    turns a settings form's sections into tabs.

   Both are progressive enhancements: with JS off, every step / panel is
   simply visible and the form posts exactly the same fields. Nothing here
   stores state on the server — a wizard is still ONE form and ONE submit,
   so a half-finished product can never be written to disk.
   ======================================================================== */
(function () {
  "use strict";

  /* ------------------------------------------------------------- wizard -- */

  function wizard(form) {
    var steps = Array.prototype.filter.call(form.children, function (el) {
      return el.classList.contains("step");
    });
    if (steps.length < 2) return;

    var stepper = form.querySelector(".stepper");
    var items = stepper ? Array.prototype.slice.call(stepper.children) : [];
    var buttons = items.map(function (li) { return li.querySelector(".step-btn"); });

    // When editing something that already exists, every step is reachable at
    // once (it behaves like tabs). When creating, you unlock as you go.
    var freeNav = form.hasAttribute("data-free-nav");
    // A page that comes back from the server mid-flow (a freshly written
    // script, say) says which step to open, so you land on the result rather
    // than at the top being asked the same questions again.
    var start = parseInt(form.getAttribute("data-start-step"), 10) || 0;
    var current = Math.min(Math.max(start, 0), steps.length - 1);
    var furthest = freeNav ? steps.length - 1 : current;

    form.classList.add("is-wizard");

    function paint() {
      steps.forEach(function (step, i) { step.hidden = i !== current; });
      items.forEach(function (li, i) {
        // "done" means behind you, not merely reachable -- so free navigation
        // doesn't tick steps you have never actually looked at.
        li.dataset.state = i === current ? "current" : i < current ? "done" : "todo";
        var btn = buttons[i];
        if (!btn) return;
        btn.disabled = i > furthest;
        if (i === current) btn.setAttribute("aria-current", "step");
        else btn.removeAttribute("aria-current");
      });
      form.dispatchEvent(new CustomEvent("wizard:step", { detail: { index: current, last: current === steps.length - 1 } }));
    }

    function go(next, viaUser) {
      if (next < 0 || next >= steps.length) return;
      current = next;
      if (current > furthest) furthest = current;
      paint();
      if (viaUser) {
        var heading = steps[current].querySelector("h2, h3");
        if (heading) {
          heading.setAttribute("tabindex", "-1");
          heading.focus({ preventScroll: true });
        }
        var top = form.getBoundingClientRect().top + window.scrollY - 90;
        window.scrollTo({ top: top < 0 ? 0 : top, behavior: "smooth" });
      }
    }

    /* Validate only what is on screen. Hidden fields can't be focused, so
       reporting on them would throw and trap the user with no visible error. */
    function stepIsValid(index) {
      var fields = steps[index].querySelectorAll("input, select, textarea");
      for (var i = 0; i < fields.length; i++) {
        if (!fields[i].checkValidity()) {
          if (index !== current) go(index, true);
          fields[i].reportValidity();
          return false;
        }
      }
      return true;
    }

    form.addEventListener("click", function (ev) {
      var next = ev.target.closest("[data-wizard-next]");
      var back = ev.target.closest("[data-wizard-back]");
      var jump = ev.target.closest(".step-btn");
      if (next) { ev.preventDefault(); if (stepIsValid(current)) go(current + 1, true); }
      else if (back) { ev.preventDefault(); go(current - 1, true); }
      else if (jump) { ev.preventDefault(); go(items.indexOf(jump.parentNode), true); }
    });

    // The form is novalidate so hidden steps never block the submit; instead
    // we check each step ourselves and jump to the first one with a problem.
    form.addEventListener("submit", function (ev) {
      for (var i = 0; i < steps.length; i++) {
        if (!stepIsValid(i)) { ev.preventDefault(); return; }
      }
    });

    paint();
  }

  /* --------------------------------------------------------------- tabs -- */

  function tabs(root) {
    var list = root.querySelector(".tabs");
    var panels = Array.prototype.slice.call(root.querySelectorAll(".tab-panel"));
    if (!list || panels.length < 2) return;
    var btns = Array.prototype.slice.call(list.querySelectorAll("button"));

    root.classList.add("is-tabbed");

    function select(index) {
      panels.forEach(function (p, i) { p.hidden = i !== index; });
      btns.forEach(function (b, i) { b.setAttribute("aria-selected", String(i === index)); });
    }

    list.addEventListener("click", function (ev) {
      var btn = ev.target.closest("button");
      if (btn) { ev.preventDefault(); select(btns.indexOf(btn)); }
    });

    // A field flagged invalid on submit may be sitting on a hidden panel.
    root.addEventListener("invalid", function (ev) {
      var panel = ev.target.closest(".tab-panel");
      if (panel) select(panels.indexOf(panel));
    }, true);

    select(0);
  }

  /* -------------------------------------------------------------- boot --- */

  document.querySelectorAll("form.wizard").forEach(wizard);
  document.querySelectorAll(".tabbed").forEach(tabs);
})();
