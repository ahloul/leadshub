// Live countdowns to a deadline (the review queue's 4-hour limit, FR-6.7).
//
//   <span data-countdown="2026-11-14T18:30:00+02:00" data-sla="ok">
//     <span data-countdown-time dir="ltr">2:41:07</span>
//   </span>
//
// Every second, the time left is written into [data-countdown-time] as
// h:mm:ss (-h:mm:ss once past), and data-sla becomes "ok", "near" (an hour
// or less left) or "breached", which the page's classes colour. Elements
// added later by live updates are picked up on the next tick.
(function () {
    "use strict";
    var NEAR_MS = 60 * 60 * 1000;

    function pad(n) {
        return n < 10 ? "0" + n : String(n);
    }

    function format(ms) {
        var sign = ms < 0 ? "-" : "";
        var total = Math.floor(Math.abs(ms) / 1000);
        return sign + Math.floor(total / 3600) + ":" + pad(Math.floor((total % 3600) / 60)) + ":" + pad(total % 60);
    }

    function tick() {
        var now = Date.now();
        var items = document.querySelectorAll("[data-countdown]");
        for (var i = 0; i < items.length; i++) {
            var item = items[i];
            var due = Date.parse(item.getAttribute("data-countdown"));
            if (isNaN(due)) continue;
            var left = due - now;
            var time = item.querySelector("[data-countdown-time]");
            if (time) time.textContent = format(left);
            item.setAttribute("data-sla", left <= 0 ? "breached" : left <= NEAR_MS ? "near" : "ok");
        }
    }

    tick();
    setInterval(tick, 1000);
})();
