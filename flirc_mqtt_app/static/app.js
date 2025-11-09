const contextScript = document.getElementById("app-context");
let appContext = {};
if (contextScript && contextScript.textContent) {
  try {
    appContext = JSON.parse(contextScript.textContent);
  } catch (error) {
    console.warn("Unable to parse app context", error);
  }
}
const patternsData = appContext.patterns || {};
const requiresToken = Boolean(appContext.requiresToken);
let authToken = requiresToken ? window.localStorage.getItem("webToken") : null;

function ensureAuthToken() {
  if (!requiresToken) {
    return null;
  }
  if (authToken) {
    return authToken;
  }
  const input = prompt("Enter access token");
  if (!input) {
    alert("This action requires an access token.");
    return null;
  }
  authToken = input.trim();
  if (authToken) {
    window.localStorage.setItem("webToken", authToken);
  }
  return authToken;
}

function buildHeaders(base = {}) {
  const headers = { ...(base || {}) };
  if (!requiresToken) {
    return headers;
  }
  const token = ensureAuthToken();
  if (!token) {
    return null;
  }
  headers["X-Auth-Token"] = token;
  return headers;
}

async function handleAuthResponse(response) {
  if (requiresToken && response.status === 401) {
    window.localStorage.removeItem("webToken");
    authToken = null;
    const data = await response.json().catch(() => ({}));
    alert("Authentication failed: " + (data.detail || response.statusText));
    return false;
  }
  return true;
}

async function submitPattern(event) {
  event.preventDefault();
  const device = document.getElementById("device").value;
  const action = document.getElementById("action").value;
  const formatsRaw = document.getElementById("formats").value;
  let formats;
  try {
    formats = JSON.parse(formatsRaw);
  } catch (error) {
    alert("Formats must be valid JSON");
    return false;
  }
  let headers = buildHeaders({ "Content-Type": "application/json" });
  if (requiresToken && headers === null) {
    return false;
  }
  headers = headers || { "Content-Type": "application/json" };
  const payload = { device, action, formats };
  const response = await fetch("/api/patterns", {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    if (!(await handleAuthResponse(response))) {
      return false;
    }
    const data = await response.json().catch(() => ({}));
    alert("Failed to save pattern: " + (data.detail || response.statusText));
  } else {
    window.location.reload();
  }
  return false;
}

async function sendPattern(device, action) {
  const response = await fetch("/api/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device, action }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    alert("Failed to send pattern: " + (data.detail || response.statusText));
  }
}

function editPattern(device, action) {
  document.getElementById("device").value = device;
  document.getElementById("action").value = action;
  const formats = patternsData[device][action];
  const formatsArray = Object.entries(formats).map(([format, info]) => {
    const entry =
      info && typeof info === "object" && !Array.isArray(info)
        ? info
        : { data: info };
    const payload = { format, data: entry.data };
    if (entry.hash) {
      payload.hash = entry.hash;
    }
    return payload;
  });
  document.getElementById("formats").value = JSON.stringify(
    formatsArray,
    null,
    2
  );
  const form = patternForm || document.getElementById("pattern-form");
  if (form) {
    window.scrollTo({ top: form.offsetTop, behavior: "smooth" });
  }
}

async function deleteAction(device, action) {
  if (!confirm(`Delete pattern ${device} / ${action}?`)) {
    return;
  }
  let headers = buildHeaders();
  if (requiresToken && headers === null) {
    return;
  }
  headers = headers || {};
  const response = await fetch(
    `/api/patterns/${encodeURIComponent(device)}/${encodeURIComponent(action)}`,
    {
      method: "DELETE",
      headers,
    }
  );
  if (!response.ok) {
    if (!(await handleAuthResponse(response))) {
      return;
    }
    const data = await response.json().catch(() => ({}));
    alert("Failed to delete pattern: " + (data.detail || response.statusText));
  } else {
    window.location.reload();
  }
}

async function deleteDevice(device) {
  if (!confirm(`Delete all patterns for device ${device}?`)) {
    return;
  }
  let headers = buildHeaders();
  if (requiresToken && headers === null) {
    return;
  }
  headers = headers || {};
  const actions = Object.keys(patternsData[device] || {});
  for (const action of actions) {
    const response = await fetch(
      `/api/patterns/${encodeURIComponent(device)}/${encodeURIComponent(
        action
      )}`,
      {
        method: "DELETE",
        headers,
      }
    );
    if (!response.ok) {
      if (!(await handleAuthResponse(response))) {
        return;
      }
      const data = await response.json().catch(() => ({}));
      alert(
        `Failed to delete ${device}/${action}: ` +
          (data.detail || response.statusText)
      );
      return;
    }
  }
  window.location.reload();
}

const patternForm = document.getElementById("pattern-form");
if (patternForm) {
  patternForm.addEventListener("submit", submitPattern);
}

window.sendPattern = sendPattern;
window.editPattern = editPattern;
window.deleteAction = deleteAction;
window.deleteDevice = deleteDevice;
