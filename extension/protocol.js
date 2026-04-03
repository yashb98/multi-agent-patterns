// extension/protocol.js
// Message types shared between background, content, and popup scripts.

const MSG = Object.freeze({
  // Python -> Extension commands
  CMD_NAVIGATE: "navigate",
  CMD_FILL: "fill",
  CMD_CLICK: "click",
  CMD_UPLOAD: "upload",
  CMD_SCREENSHOT: "screenshot",
  CMD_SELECT: "select",
  CMD_CHECK: "check",
  CMD_SCROLL: "scroll",
  CMD_WAIT: "wait",
  CMD_CLOSE_TAB: "close_tab",

  // Extension -> Python response types
  RESP_ACK: "ack",
  RESP_RESULT: "result",
  RESP_SNAPSHOT: "snapshot",
  RESP_NAVIGATION: "navigation",
  RESP_MUTATION: "mutation",
  RESP_ERROR: "error",
  RESP_PONG: "pong",

  // Internal messages (background <-> content/popup/sidepanel)
  INT_STATUS: "status",
  INT_CONNECT: "connect",
  INT_DISCONNECT: "disconnect",
  INT_SNAPSHOT_UPDATE: "snapshot_update",
  INT_FIELD_FILLED: "field_filled",
  INT_APPLICATION_START: "application_start",
  INT_APPLICATION_COMPLETE: "application_complete",
});

// Connection states
const CONNECTION = Object.freeze({
  DISCONNECTED: "disconnected",
  CONNECTING: "connecting",
  CONNECTED: "connected",
});
