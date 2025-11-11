const contextScript = document.getElementById("app-context");
let appContext = {};
if (contextScript && contextScript.textContent) {
  try {
    appContext = JSON.parse(contextScript.textContent);
  } catch (error) {
    console.warn("Unable to parse app context", error);
  }
}
const devicesData = appContext.devices || [];
const requiresToken = Boolean(appContext.requiresToken);
let authToken = requiresToken ? window.localStorage.getItem("webToken") : null;
const alertsRoot = document.getElementById("alerts-root");
const deviceInput = document.getElementById("device");
const actionInput = document.getElementById("action");
const patternFormatSelect = document.getElementById("pattern-format");
const patternDataInput = document.getElementById("pattern-data");
const patternRepeatInput = document.getElementById("pattern-repeat");
const patternIkInput = document.getElementById("pattern-ik");
const deviceIdField = document.getElementById("device-id-field");
const actionIdField = document.getElementById("action-id-field");
const patternIdField = document.getElementById("pattern-id-field");

const deviceMap = new Map();
const actionMap = new Map();
const patternMap = new Map();

devicesData.forEach((device) => {
  if (!device || !device.id) {
    return;
  }
  deviceMap.set(device.id, device);
  (device.actions || []).forEach((action) => {
    if (!action || !action.id) {
      return;
    }
    action.device_id = action.device_id || device.id;
    action.device_name = device.name;
    actionMap.set(action.id, action);
    (action.patterns || []).forEach((pattern) => {
      if (!pattern || !pattern.id) {
        return;
      }
      pattern.action_id = pattern.action_id || action.id;
      pattern.device_id = device.id;
      patternMap.set(pattern.id, pattern);
    });
  });
});

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
    showToast(
      "Authentication failed: " + (data.detail || response.statusText),
      "danger",
      5000
    );
    return false;
  }
  return true;
}

async function submitPattern(event) {
  event.preventDefault();
  if (!patternFormatSelect || !patternDataInput) {
    showToast("Pattern form is not available on this page.", "danger");
    return false;
  }

  const deviceName = deviceInput ? deviceInput.value.trim() : "";
  const actionName = actionInput ? actionInput.value.trim() : "";
  const deviceId = deviceIdField ? deviceIdField.value.trim() : "";
  const actionId = actionIdField ? actionIdField.value.trim() : "";
  const patternId = patternIdField ? patternIdField.value.trim() : "";
  const formatValue = patternFormatSelect.value;
  const dataRaw = patternDataInput.value;
  const repeatRaw = patternRepeatInput ? patternRepeatInput.value : "";
  const ikRaw = patternIkInput ? patternIkInput.value : "";

  if (!deviceId && !deviceName) {
    showToast("Device is required.", "warning");
    return false;
  }
  if (!actionId && !actionName) {
    showToast("Action is required.", "warning");
    return false;
  }

  const normalized = normalizePatternPayload(formatValue, dataRaw);
  if (normalized.error) {
    showToast(normalized.error, "warning");
    return false;
  }

  let repeatValue = null;
  if (repeatRaw !== "" && repeatRaw !== null && repeatRaw !== undefined) {
    repeatValue = Number(repeatRaw);
    if (Number.isNaN(repeatValue) || repeatValue < 1) {
      showToast("Repeat must be a positive integer.", "warning");
      return false;
    }
  }

  let ikValue = null;
  if (ikRaw !== "" && ikRaw !== null && ikRaw !== undefined) {
    ikValue = Number(ikRaw);
    if (Number.isNaN(ikValue) || ikValue <= 0) {
      showToast("IK must be a positive integer.", "warning");
      return false;
    }
  }

  const patternPayload = {
    id: patternId || undefined,
    format: formatValue,
    data: normalized.data,
    repeat: repeatValue || 1,
    ik: ikValue || 23000,
  };

  const payload = {
    device_id: deviceId || undefined,
    device: deviceName || undefined,
    action_id: actionId || undefined,
    action: actionName || undefined,
    patterns: [patternPayload],
  };

  let headers = buildHeaders({ "Content-Type": "application/json" });
  if (requiresToken && headers === null) {
    return false;
  }
  headers = headers || { "Content-Type": "application/json" };

  const endpoint = patternId
    ? `/api/pattern?pattern=${encodeURIComponent(patternId)}`
    : "/api/pattern";
  const method = patternId ? "PUT" : "POST";

  const response = await fetch(endpoint, {
    method,
    headers,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    if (!(await handleAuthResponse(response))) {
      return false;
    }
    const data = await response.json().catch(() => ({}));
    showToast(
      "Failed to save pattern: " + (data.detail || response.statusText),
      "danger",
      5000
    );
  } else {
    window.location.reload();
  }
  return false;
}

async function sendAction(deviceId, actionId) {
  let headers = buildHeaders({ "Content-Type": "application/json" });
  if (requiresToken && headers === null) {
    return;
  }
  headers = headers || { "Content-Type": "application/json" };
  const device = deviceMap.get(deviceId);
  const action = actionMap.get(actionId);
  try {
    const response = await fetch("/api/send", {
      method: "POST",
      headers,
      body: JSON.stringify({ device: deviceId, action: actionId }),
    });
    if (!response.ok) {
      if (!(await handleAuthResponse(response))) {
        return;
      }
      const data = await response.json().catch(() => ({}));
      showToast(
        "Failed to send action: " + (data.detail || response.statusText),
        "danger",
        5000
      );
    } else {
      const deviceLabel = device ? device.name : deviceId;
      const actionLabel = action ? action.name : actionId;
      showToast(`Sent ${deviceLabel}/${actionLabel}`, "success", 2500);
    }
  } catch (error) {
    showToast(`Failed to send action: ${error}`, "danger", 5000);
  }
}

async function sendPatternById(patternId) {
  let headers = buildHeaders({ "Content-Type": "application/json" });
  if (requiresToken && headers === null) {
    return;
  }
  headers = headers || { "Content-Type": "application/json" };
  const pattern = patternMap.get(patternId);
  const action = pattern ? actionMap.get(pattern.action_id) : null;
  const device = action ? deviceMap.get(action.device_id) : null;
  try {
    const response = await fetch("/api/send", {
      method: "POST",
      headers,
      body: JSON.stringify({ pattern: patternId }),
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
      const labelParts = [];
      if (device) {
        labelParts.push(device.name);
      }
      if (action) {
        labelParts.push(action.name);
      }
      const label =
        labelParts.length > 0 ? labelParts.join(" / ") : `Pattern ${patternId}`;
      showToast(`Sent ${label}`, "success", 2500);
    }
  } catch (error) {
    showToast(`Failed to send pattern: ${error}`, "danger", 5000);
  }
}

function editPattern(patternId) {
  const pattern = patternMap.get(patternId);
  if (!pattern) {
    showToast("Pattern not found.", "warning");
    return;
  }
  const action = actionMap.get(pattern.action_id);
  const device = action ? deviceMap.get(action.device_id) : null;

  if (deviceInput) {
    deviceInput.value = device ? device.name : "";
  }
  if (actionInput) {
    actionInput.value = action ? action.name : "";
  }
  if (patternFormatSelect) {
    patternFormatSelect.value = pattern.format || "raw";
  }
  if (patternRepeatInput) {
    patternRepeatInput.value = pattern.repeat || 1;
  }
  if (patternIkInput) {
    patternIkInput.value = pattern.ik || 23000;
  }
  if (patternDataInput) {
    patternDataInput.value = patternDataToString(pattern.format, pattern.data);
  }
  if (deviceIdField) {
    deviceIdField.value = device ? device.id : "";
  }
  if (actionIdField) {
    actionIdField.value = action ? action.id : "";
  }
  if (patternIdField) {
    patternIdField.value = pattern.id;
  }

  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

async function deleteActionById(actionId) {
  if (!confirm("Delete this action and all associated patterns?")) {
    return;
  }
  let headers = buildHeaders();
  if (requiresToken && headers === null) {
    return;
  }
  headers = headers || {};
  const response = await fetch(`/api/actions/${encodeURIComponent(actionId)}`, {
    method: "DELETE",
    headers,
  });
  if (!response.ok) {
    if (!(await handleAuthResponse(response))) {
      return;
    }
    const data = await response.json().catch(() => ({}));
    showToast(
      "Failed to delete action: " + (data.detail || response.statusText),
      "danger",
      5000
    );
  } else {
    window.location.reload();
  }
}

async function deleteDeviceById(deviceId) {
  if (!confirm("Delete this device and all associated actions and patterns?")) {
    return;
  }
  let headers = buildHeaders();
  if (requiresToken && headers === null) {
    return;
  }
  headers = headers || {};
  const response = await fetch(`/api/devices/${encodeURIComponent(deviceId)}`, {
    method: "DELETE",
    headers,
  });
  if (!response.ok) {
    if (!(await handleAuthResponse(response))) {
      return;
    }
    const data = await response.json().catch(() => ({}));
    showToast(
      "Failed to delete device: " + (data.detail || response.statusText),
      "danger",
      5000
    );
  } else {
    window.location.reload();
  }
}

async function deletePatternById(patternId) {
  if (!confirm("Delete this pattern?")) {
    return;
  }
  let headers = buildHeaders({ "Content-Type": "application/json" });
  if (requiresToken && headers === null) {
    return;
  }
  headers = headers || { "Content-Type": "application/json" };
  try {
    const response = await fetch(
      `/api/pattern?pattern=${encodeURIComponent(patternId)}`,
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
      return;
    }
    showToast("Deleted pattern", "success", 2500);
    window.location.reload();
  } catch (error) {
    showToast(`Failed to delete pattern: ${error}`, "danger", 5000);
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

function patternDataToString(format, data) {
  if (!Array.isArray(data) || data.length === 0) {
    return "";
  }
  const fmt = (format || "").toLowerCase();
  if (fmt === "csv" || fmt === "pronto") {
    return data.join(",");
  }
  return data.join(" ");
}

const patternForm = document.getElementById("pattern-form");
if (patternForm) {
  patternForm.addEventListener("submit", submitPattern);
}

window.sendAction = sendAction;
window.sendPatternById = sendPatternById;
window.editPattern = editPattern;
window.deleteActionById = deleteActionById;
window.deleteDeviceById = deleteDeviceById;
window.deletePatternById = deletePatternById;
window.scrollToAddPattern = scrollToAddPattern;

function scrollToAddPattern() {
  if (deviceInput) {
    deviceInput.value = "";
    deviceInput.focus();
  }
  if (deviceIdField) {
    deviceIdField.value = "";
  }
  if (actionIdField) {
    actionIdField.value = "";
  }
  if (patternIdField) {
    patternIdField.value = "";
  }
  if (actionInput) {
    actionInput.value = "";
  }
  if (patternRepeatInput) {
    patternRepeatInput.value = "1";
  }
  if (patternIkInput) {
    patternIkInput.value = "23000";
  }
  if (patternDataInput) {
    patternDataInput.value = "";
  }
  if (patternFormatSelect) {
    patternFormatSelect.value = "raw";
  }
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

const customSendForm = document.getElementById("custom-send-form");
const customFormatSelect = document.getElementById("custom-format-select");
const customPatternInput = document.getElementById("custom-pattern-input");
const customRepeatInput = document.getElementById("custom-repeat-input");
const customIkInput = document.getElementById("custom-ik-input");
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

  let repeatValue = null;
  if (customRepeatInput && customRepeatInput.value !== "") {
    repeatValue = Number(customRepeatInput.value);
    if (Number.isNaN(repeatValue) || repeatValue < 1) {
      showToast("Repeat must be a positive integer.", "warning");
      return false;
    }
  }

  let ikValue = null;
  if (customIkInput && customIkInput.value !== "") {
    ikValue = Number(customIkInput.value);
    if (Number.isNaN(ikValue) || ikValue <= 0) {
      showToast("IK must be a positive integer.", "warning");
      return false;
    }
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
  if (repeatValue !== null) {
    payload.repeat = repeatValue;
  }
  if (ikValue !== null) {
    payload.ik = ikValue;
  }

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
    if (customRepeatInput) {
      customRepeatInput.value = customRepeatInput.value || "1";
    }
    if (customIkInput) {
      customIkInput.value = customIkInput.value || "23000";
    }
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

if (patternDataInput) {
  const handlePatternDataInput = () => {
    const detected = detectFormatFromValue(patternDataInput.value);
    if (
      detected &&
      patternFormatSelect &&
      detected !== patternFormatSelect.value
    ) {
      patternFormatSelect.value = detected;
    }
  };
  patternDataInput.addEventListener("input", handlePatternDataInput);
  patternDataInput.addEventListener("paste", () => {
    setTimeout(handlePatternDataInput, 0);
  });
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
    button.style.color = "#ffffff";
    button.style.boxShadow = "0 0.25rem 0.5rem rgba(0,0,0,0.2)";
  });
}

applyActionButtonStyles();