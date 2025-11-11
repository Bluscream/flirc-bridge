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
const alertsRoot = document.getElementById("alerts-root");

function showToast(message, variant = "info", delay = 4000) {
  if (!alertsRoot || typeof bootstrap === "undefined" || !bootstrap.Toast) {
    window.alert(message);
    return;
  }

  const toastElement = document.createElement("div");
  toastElement.className = `toast align-items-center text-bg-${variant} border-0 shadow`;
  toastElement.setAttribute("role", "alert");
  toastElement.setAttribute("aria-live", "assertive");
  toastElement.setAttribute("aria-atomic", "true");

  const wrapper = document.createElement("div");
  wrapper.className = "d-flex";

  const body = document.createElement("div");
  body.className = "toast-body";
  body.textContent = message;

  const closeButton = document.createElement("button");
  closeButton.type = "button";
  closeButton.className = "btn-close btn-close-white me-2 m-auto";
  closeButton.setAttribute("data-bs-dismiss", "toast");
  closeButton.setAttribute("aria-label", "Close");

  wrapper.appendChild(body);
  wrapper.appendChild(closeButton);
  toastElement.appendChild(wrapper);
  alertsRoot.appendChild(toastElement);

  const toast = new bootstrap.Toast(toastElement, { delay });
  toastElement.addEventListener("hidden.bs.toast", () => toastElement.remove());
  toast.show();
}

function ensureAuthToken() {
  if (!requiresToken) {
    return null;
  }
  if (authToken) {
    return authToken;
  }
  const input = prompt("Enter access token");
  if (!input) {
    showToast("This action requires an access token.", "warning");
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
    showToast("Authentication failed: " + (data.detail || response.statusText), "danger", 5000);
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
    showToast("Formats must be valid JSON.", "warning");
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
    showToast("Failed to save pattern: " + (data.detail || response.statusText), "danger", 5000);
  } else {
    window.location.reload();
  }
  return false;
}

async function sendPattern(device, action) {
  let headers = buildHeaders({ "Content-Type": "application/json" });
  if (requiresToken && headers === null) {
    return;
  }
  headers = headers || { "Content-Type": "application/json" };
  try {
    const response = await fetch("/api/send", {
      method: "POST",
      headers,
      body: JSON.stringify({ device, action }),
    });
    if (!response.ok) {
      if (!(await handleAuthResponse(response))) {
        return;
      }
      const data = await response.json().catch(() => ({}));
      showToast(
        "Failed to send pattern: " + (data.detail || response.statusText),
        "danger",
        5000
      );
    } else {
      showToast(`Sent ${device}/${action}`, "success", 2500);
    }
  } catch (error) {
    showToast(`Failed to send pattern: ${error}`, "danger", 5000);
  }
}

async function sendPatternFormat(device, action, formatName) {
  const deviceEntry = patternsData?.[device];
  if (!deviceEntry) {
    showToast(`No patterns found for device ${device}`, "warning");
    return;
  }
  const actionEntry = deviceEntry?.[action];
  if (!actionEntry) {
    showToast(`No action ${action} for device ${device}`, "warning");
    return;
  }
  const formatEntry = actionEntry?.[formatName];
  if (!formatEntry) {
    showToast(`No format ${formatName} for ${device}/${action}`, "warning");
    return;
  }

  const values =
    formatEntry &&
    typeof formatEntry === "object" &&
    !Array.isArray(formatEntry)
      ? formatEntry.data
      : formatEntry;

  if (!values) {
    showToast(
      `Pattern data missing for ${device}/${action} (${formatName})`,
      "warning"
    );
    return;
  }

  const normalizedData = Array.isArray(values)
    ? values.map((item) => String(item))
    : [String(values)];

  const payload = {
    format: (formatName || "").toLowerCase(),
    data: normalizedData,
  };

  let headers = buildHeaders({ "Content-Type": "application/json" });
  if (requiresToken && headers === null) {
    return;
  }
  headers = headers || { "Content-Type": "application/json" };

  try {
    const response = await fetch("/api/send", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      if (!(await handleAuthResponse(response))) {
        return;
      }
      const data = await response.json().catch(() => ({}));
      showToast(
        `Failed to send ${device}/${action} (${formatName}): ` +
          (data.detail || response.statusText),
        "danger",
        5000
      );
    } else {
      showToast(`Sent ${device}/${action} (${formatName})`, "success", 2500);
    }
  } catch (error) {
    showToast(
      `Failed to send ${device}/${action} (${formatName}): ${error}`,
      "danger",
      5000
    );
  }
}

function editPattern(device, action) {
  if (!patternsData[device] || !patternsData[device][action]) {
    showToast(`Pattern ${device}/${action} not found`, "warning");
    return;
  }
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
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
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
    showToast(
      "Failed to delete pattern: " + (data.detail || response.statusText),
      "danger",
      5000
    );
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
      showToast(
        `Failed to delete ${device}/${action}: ${
          data.detail || response.statusText
        }`,
        "danger",
        5000
      );
      return;
    }
  }
  window.location.reload();
}

async function deletePatternFormat(device, action, formatName) {
  if (!confirm(`Delete ${formatName} format for ${device}/${action}?`)) {
    return;
  }
  let headers = buildHeaders({ "Content-Type": "application/json" });
  if (requiresToken && headers === null) {
    return;
  }
  headers = headers || { "Content-Type": "application/json" };

  try {
    const response = await fetch(
      `/api/patterns/${encodeURIComponent(device)}/${encodeURIComponent(
        action
      )}/${encodeURIComponent(formatName)}`,
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
      showToast(
        `Failed to delete ${device}/${action} (${formatName}): ` +
          (data.detail || response.statusText),
        "danger",
        5000
      );
      return;
    }
    showToast(`Deleted ${device}/${action} (${formatName})`, "success", 2500);
    window.location.reload();
  } catch (error) {
    showToast(
      `Failed to delete ${device}/${action} (${formatName}): ${error}`,
      "danger",
      5000
    );
  }
}

function detectFormatFromValue(value) {
  if (!value) {
    return null;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  if (trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed) && parsed.length) {
        const flattened = parsed
          .flat(Infinity)
          .map((item) => String(item ?? ""));
        if (flattened.every((item) => /^[-+]/.test(item))) {
          return "raw";
        }
        if (flattened.every((item) => /^[0-9a-fA-F]+$/.test(item))) {
          return "pronto";
        }
        return "csv";
      }
    } catch (error) {
      // ignore JSON parse issues during detection
    }
  }
  const condensed = trimmed.replace(/\s+/g, "").toLowerCase();
  if (/^0000/.test(condensed) || /[a-f]/.test(condensed)) {
    return "pronto";
  }
  if (/[+-]/.test(trimmed)) {
    return "raw";
  }
  if (trimmed.includes(",")) {
    return "csv";
  }
  if (/^[0-9\s]+$/.test(trimmed) && trimmed.includes(" ")) {
    return "raw";
  }
  return null;
}

function normalizePatternPayload(format, rawValue) {
  const trimmed = (rawValue || "").trim();
  if (!trimmed) {
    return { error: "Pattern data is required." };
  }

  const toStrings = (items) =>
    items
      .map((entry) => {
        if (entry === null || entry === undefined) {
          return null;
        }
        if (Array.isArray(entry)) {
          return entry
            .map((sub) =>
              sub === null || sub === undefined ? null : String(sub)
            )
            .filter(Boolean);
        }
        return String(entry);
      })
      .flat()
      .filter(Boolean);

  if (trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed)) {
        const flattened = toStrings(parsed);
        if (flattened.length) {
          return { data: flattened };
        }
      }
    } catch (error) {
      return { error: "Unable to parse JSON pattern." };
    }
  }

  const lowerFormat = (format || "raw").toLowerCase();
  let parts = [];
  if (lowerFormat === "pronto") {
    parts = trimmed
      .replace(/\s+/g, ",")
      .split(/,+/)
      .map((item) => item.trim())
      .filter(Boolean)
      .map((item) => item.toUpperCase());
  } else if (lowerFormat === "csv") {
    parts = trimmed
      .replace(/\s+/g, ",")
      .split(/,+/)
      .map((item) => item.trim())
      .filter(Boolean);
  } else {
    parts = trimmed
      .split(/\s+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  if (!parts.length) {
    return { error: "Pattern data could not be parsed." };
  }

  return { data: parts };
}

const patternForm = document.getElementById("pattern-form");
if (patternForm) {
  patternForm.addEventListener("submit", submitPattern);
}

window.sendPattern = sendPattern;
window.sendPatternFormat = sendPatternFormat;
window.editPattern = editPattern;
window.deleteAction = deleteAction;
window.deleteDevice = deleteDevice;
window.scrollToAddPattern = scrollToAddPattern;

function scrollToAddPattern() {
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

const customSendForm = document.getElementById("custom-send-form");
const customFormatSelect = document.getElementById("custom-format-select");
const customPatternInput = document.getElementById("custom-pattern-input");
const customSendButton = document.getElementById("custom-send-button");

async function sendCustomPattern(event) {
  event.preventDefault();
  if (!customPatternInput) {
    return false;
  }
  const rawValue = customPatternInput.value.trim();
  if (!rawValue) {
    showToast("Pattern data is required.", "warning");
    return false;
  }

  let selectedFormat = customFormatSelect ? customFormatSelect.value : "raw";
  const detected = detectFormatFromValue(rawValue);
  if (detected && customFormatSelect && detected !== customFormatSelect.value) {
    customFormatSelect.value = detected;
    selectedFormat = detected;
  }

  const normalized = normalizePatternPayload(selectedFormat, rawValue);
  if (normalized.error) {
    showToast(normalized.error, "warning");
    return false;
  }

  let headers = buildHeaders({ "Content-Type": "application/json" });
  if (requiresToken && headers === null) {
    return false;
  }
  headers = headers || { "Content-Type": "application/json" };

  const payload = {
    format: selectedFormat,
    data: normalized.data,
  };

  if (customSendButton) {
    customSendButton.disabled = true;
  }

  try {
    const response = await fetch("/api/send", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      if (!(await handleAuthResponse(response))) {
        return false;
      }
      const data = await response.json().catch(() => ({}));
      showToast(
        "Failed to send custom pattern: " +
          (data.detail || response.statusText),
        "danger",
        5000
      );
      return false;
    }
    showToast("Custom pattern sent", "success", 2500);
    customPatternInput.value = "";
    customPatternInput.focus();
    return true;
  } catch (error) {
    showToast(`Failed to send custom pattern: ${error}`, "danger", 5000);
    return false;
  } finally {
    if (customSendButton) {
      customSendButton.disabled = false;
    }
  }
}

if (customPatternInput) {
  const handleCustomPatternInput = () => {
    const detected = detectFormatFromValue(customPatternInput.value);
    if (
      detected &&
      customFormatSelect &&
      detected !== customFormatSelect.value
    ) {
      customFormatSelect.value = detected;
    }
  };
  customPatternInput.addEventListener("input", handleCustomPatternInput);
  customPatternInput.addEventListener("paste", () => {
    setTimeout(handleCustomPatternInput, 0);
  });
  customPatternInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (customSendForm) {
        customSendForm.requestSubmit();
      }
    }
  });
}

if (customSendForm) {
  customSendForm.addEventListener("submit", sendCustomPattern);
}

function stringToHue(value) {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = value.charCodeAt(i) + ((hash << 5) - hash);
  }
  return Math.abs(hash % 360);
}

function getActionButtonColor(label) {
  if (!label) {
    return "var(--bs-primary)";
  }
  const key = label.toLowerCase();
  const hue = stringToHue(key);
  return `hsl(${hue}, 70%, 45%)`;
}

function applyActionButtonStyles() {
  const buttons = document.querySelectorAll(".action-send-button");
  buttons.forEach((button) => {
    const label = button.dataset.actionLabel || button.textContent || "";
    const color = getActionButtonColor(label.trim());
    button.style.backgroundColor = color;
    button.style.borderColor = color;
  });
}

applyActionButtonStyles();