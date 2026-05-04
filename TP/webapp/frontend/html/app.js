async function refresh() {
  const r = await fetch("/api/messages");
  const data = await r.json();
  document.getElementById("host").textContent = data.served_by;
  document.getElementById("list").innerHTML =
    data.messages.map(m => `<li>${m}</li>`).join("");
}
async function post() {
  const text = document.getElementById("msg").value;
  await fetch("/api/messages", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({text})
  });
  document.getElementById("msg").value = "";
  refresh();
}
refresh();
setInterval(refresh, 5000);