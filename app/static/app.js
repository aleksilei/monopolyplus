// Small progressive touches; everything else is plain forms and htmx.

// <form data-confirm="..."> asks before submitting.
document.addEventListener("submit", (e) => {
  const msg = e.target.dataset.confirm;
  if (msg && !window.confirm(msg)) e.preventDefault();
});

const hm = (m) => `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;

function tick() {
  // Game clock: <span data-since="ISO" data-cap="minutes">
  document.querySelectorAll("[data-since]").forEach((el) => {
    const mins = Math.max(0, Math.floor((Date.now() - Date.parse(el.dataset.since)) / 60000));
    const cap = Number(el.dataset.cap || 0);
    el.textContent = cap ? `${hm(mins)} / ${hm(cap)}` : hm(mins);
    el.classList.toggle("over", cap > 0 && mins >= cap);
    el.title = cap > 0 && mins >= cap ? "Time limit reached: count up net worth" : "Game time";
  });
  // UTC timestamps shown in the viewer's local time.
  document.querySelectorAll("time[datetime]").forEach((el) => {
    el.textContent = new Date(el.getAttribute("datetime"))
      .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  });
}

tick();
setInterval(tick, 20000);
document.addEventListener("htmx:afterSettle", tick);
