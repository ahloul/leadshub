// The sidebar drawer on phones and tablets (base.html). On laptops the
// sidebar is always shown and this does nothing visible.
(function () {
    var wide = window.matchMedia("(min-width: 64rem)"); // Tailwind's lg

    function isOpen() {
        var sidebar = document.getElementById("sidebar");
        return !!sidebar && sidebar.hasAttribute("data-open");
    }

    function setOpen(open) {
        var sidebar = document.getElementById("sidebar");
        if (!sidebar) return;
        var opener = document.querySelector("[data-sidebar-open]");
        sidebar.toggleAttribute("data-open", open);
        document.getElementById("sidebar-backdrop").toggleAttribute("data-open", open);
        if (opener) opener.setAttribute("aria-expanded", open ? "true" : "false");
        // Keep the page behind the drawer from scrolling.
        document.documentElement.classList.toggle("overflow-hidden", open && !wide.matches);
        if (open) {
            var first = sidebar.querySelector("[aria-current=page], nav a");
            if (first) first.focus();
        } else if (opener && sidebar.contains(document.activeElement)) {
            opener.focus();
        }
    }

    // Capture phase: djust handles dj-navigate clicks on the links, and the
    // drawer should still close after one.
    document.addEventListener("click", function (event) {
        if (event.target.closest("[data-sidebar-open]")) {
            setOpen(true);
        } else if (isOpen() && event.target.closest("[data-sidebar-close], #sidebar nav a")) {
            setOpen(false);
        }
    }, true);

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && isOpen()) setOpen(false);
    });

    wide.addEventListener("change", function () {
        if (isOpen()) setOpen(false);
    });
})();
