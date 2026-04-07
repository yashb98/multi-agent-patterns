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
  CMD_ANALYZE_FIELD: "analyze_field",
  CMD_GET_SNAPSHOT: "get_snapshot",

  // v2 form engine commands
  CMD_FILL_RADIO_GROUP: "fill_radio_group",
  CMD_FILL_CUSTOM_SELECT: "fill_custom_select",
  CMD_FILL_AUTOCOMPLETE: "fill_autocomplete",
  CMD_FILL_TAG_INPUT: "fill_tag_input",
  CMD_FILL_DATE: "fill_date",
  CMD_SCROLL_TO: "scroll_to",
  CMD_WAIT_FOR_SELECTOR: "wait_for_selector",
  CMD_GET_FIELD_CONTEXT: "get_field_context",
  CMD_SCAN_FORM_GROUPS: "scan_form_groups",
  CMD_CHECK_CONSENT_BOXES: "check_consent_boxes",
  CMD_FORCE_CLICK: "force_click",
  CMD_RESCAN_AFTER_FILL: "rescan_after_fill",

  // MV3 state persistence
  CMD_SAVE_FORM_PROGRESS: "save_form_progress",
  CMD_GET_FORM_PROGRESS: "get_form_progress",
  CMD_CLEAR_FORM_PROGRESS: "clear_form_progress",

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
