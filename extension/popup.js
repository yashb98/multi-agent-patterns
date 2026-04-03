// extension/popup.js

const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const btnConnect = document.getElementById("btn-connect");
const btnDisconnect = document.getElementById("btn-disconnect");
const infoText = document.getElementById("info-text");

function updateUI(state) {
  const statusEl = document.getElementById("status");
  statusEl.className = "status " + state;
  if (state === CONNECTION.CONNECTED) {
    statusText.textContent = "Connected";
    btnConnect.disabled = true;
    btnDisconnect.disabled = false;
    infoText.textContent = "Extension is linked to Python backend.";
  } else if (state === CONNECTION.CONNECTING) {
    statusText.textContent = "Connecting...";
    btnConnect.disabled = true;
    btnDisconnect.disabled = true;
    infoText.textContent = "Establishing WebSocket connection...";
  } else {
    statusText.textContent = "Disconnected";
    btnConnect.disabled = false;
    btnDisconnect.disabled = true;
    infoText.textContent = "Click Connect to link with Python backend.";
  }
}

// Get current status from background
chrome.runtime.sendMessage({ type: MSG.INT_STATUS }, (resp) => {
  if (resp && resp.state) updateUI(resp.state);
});

btnConnect.addEventListener("click", () => {
  updateUI(CONNECTION.CONNECTING);
  chrome.runtime.sendMessage({ type: MSG.INT_CONNECT });
});

btnDisconnect.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: MSG.INT_DISCONNECT });
  updateUI(CONNECTION.DISCONNECTED);
});

// Listen for status updates
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === MSG.INT_STATUS && msg.state) {
    updateUI(msg.state);
  }
});
