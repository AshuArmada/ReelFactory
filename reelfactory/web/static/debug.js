/* Read-only polling; textContent keeps provider errors out of HTML execution. */
(function () {
  "use strict";
  var select = document.getElementById("debug-run");
  var live = document.getElementById("debug-live");
  var summary = document.getElementById("debug-summary");
  var events = document.getElementById("debug-events");
  var connection = document.getElementById("debug-connection");
  var runs = [];
  function draw() {
    var run = runs.find(function (r) { return r.id === select.value; }) || runs[0];
    events.replaceChildren();
    if (!run) { summary.textContent = "No activity yet. Make a change or generate a script in the main tab."; return; }
    summary.textContent = run.status.toUpperCase() + " · " + run.seconds + "s · Reference " + run.id
      + (run.dropped ? " · " + run.dropped + " earlier events omitted" : "");
    run.events.forEach(function (row) {
      var li = document.createElement("li");
      var title = document.createElement("strong");
      title.textContent = "+" + row.elapsed + "s — " + row.label;
      var details = document.createElement("p");
      details.className = "hint";
      details.textContent = Object.entries(row.details).map(function (pair) { return pair[0] + ": " + pair[1]; }).join(" · ");
      li.append(title, details); events.append(li);
    });
  }
  select.addEventListener("change", draw);
  async function poll() {
    try {
      if (live.checked) {
        var response = await fetch("/debug/data", {cache: "no-store", signal: AbortSignal.timeout(8000)});
        if (!response.ok) throw new Error("Connection failed");
        runs = (await response.json()).runs;
        var selected = select.value;
        select.replaceChildren(new Option("Latest request", ""));
        runs.forEach(function (run) {
          select.add(new Option(run.started + " · " + run.route + " · " + run.status, run.id));
        });
        select.value = runs.some(function (r) { return r.id === selected; }) ? selected : "";
        connection.textContent = "Connected · refreshes every 2 seconds";
        draw();
      } else { connection.textContent = "Updates paused"; }
    } catch (_) { connection.textContent = "Cannot reach the app. Retrying…"; }
    window.setTimeout(poll, 2000);
  }
  poll();
}());
