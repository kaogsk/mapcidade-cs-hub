const login = document.querySelector("#login");
const sections = document.querySelector("#sections");
const results = document.querySelector("#results");
const status = document.querySelector("#status");
let token;

async function request(path, options = {}) {
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

async function show(path) {
  status.textContent = "Loading…";
  results.textContent = "";
  try {
    results.textContent = JSON.stringify(await request(path), null, 2);
    status.textContent = "";
  } catch (error) {
    status.textContent = error.message;
  }
}

login.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const body = JSON.stringify(Object.fromEntries(new FormData(login)));
    ({ token } = await request("/api/auth/login", { method: "POST", body }));
    login.hidden = true;
    sections.hidden = false;
    await show("/api/accounts");
  } catch (error) {
    status.textContent = error.message;
  }
});

sections.addEventListener("click", async ({ target }) => {
  if (target.id === "logout") {
    try {
      await request("/api/auth/logout", { method: "POST" });
      token = undefined;
      sections.hidden = true;
      login.hidden = false;
      status.textContent = "";
      results.textContent = "";
    } catch (error) {
      status.textContent = error.message;
    }
  } else if (target.value) {
    await show(target.value);
  }
});
