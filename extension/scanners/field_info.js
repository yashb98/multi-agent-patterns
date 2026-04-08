// extension/scanners/field_info.js — Extract structured field info from a form element
//
// Depends on: core/dom.js, scanners/field_context.js
// Exports: window.JobPulse.scanners.fieldInfo.extractFieldInfo

window.JobPulse = window.JobPulse || {};
window.JobPulse.scanners = window.JobPulse.scanners || {};

/**
 * Extract structured field info from a form element.
 * Maps HTML input types + ARIA roles to our FieldInfo schema.
 */
function extractFieldInfo(el, iframeIndex) {
  const JP = window.JobPulse;
  const tag = el.tagName.toLowerCase();

  // Determine input type from tag, type attribute, and ARIA role
  let inputType = "text";
  if (tag === "select") inputType = "select";
  else if (tag === "textarea") inputType = "textarea";
  else if (el.getAttribute("contenteditable") === "true") inputType = "rich_text";
  else if (el.getAttribute("role") === "listbox") inputType = "custom_select";
  else if (el.getAttribute("role") === "combobox") inputType = "search_autocomplete";
  else if (el.getAttribute("role") === "radiogroup") inputType = "radio";
  else if (el.getAttribute("role") === "switch") inputType = "toggle";
  else inputType = (el.getAttribute("type") || "text").toLowerCase();

  // ── Dynamic DOM context extraction ──
  // Instead of hardcoded strategies, we scrape EVERYTHING around the field.
  // The LLM decides what's relevant. The content script is a thorough scanner.
  const domContext = JP.scanners.fieldContext.extractFieldContext(el);
  let label = domContext.label;

  // Clean up label
  label = label.replace(/\s*\*\s*$/, "").replace(/\s+/g, " ").trim();
  label = label.replace(/\s*Please enter a valid.*$/i, "").trim();

  // Extract <select> options (skip placeholder "Select..." options)
  const options = [];
  if (tag === "select") {
    el.querySelectorAll("option").forEach((opt) => {
      const text = opt.textContent.trim();
      if (text && !text.toLowerCase().startsWith("select")) options.push(text);
    });
  }

  // Build a unique CSS selector for this element
  let selector = "";
  if (el.id) {
    // Use attribute selector for IDs with special chars (React uses :r4: etc)
    selector = /[:#.\[\]]/.test(el.id) || /^\d/.test(el.id) ? `[id="${el.id}"]` : `#${el.id}`;
  }
  else if (el.name) selector = `${tag}[name="${el.name}"]`;
  else {
    // No id or name — build a unique selector by walking up the DOM
    // to find a parent with a distinguishing id, data attribute, or class
    let built = false;
    let ancestor = el.parentElement;
    for (let depth = 0; ancestor && depth < 8; depth++, ancestor = ancestor.parentElement) {
      let anchorSel = "";
      if (ancestor.id) {
        anchorSel = /[:#.\[\]]/.test(ancestor.id) || /^\d/.test(ancestor.id) ? `[id="${ancestor.id}"]` : `#${ancestor.id}`;
      } else if (ancestor.getAttribute("data-zcqa")) {
        anchorSel = `[data-zcqa="${ancestor.getAttribute("data-zcqa")}"]`;
      } else if (ancestor.getAttribute("data-field")) {
        anchorSel = `[data-field="${ancestor.getAttribute("data-field")}"]`;
      } else if (ancestor.getAttribute("data-name")) {
        anchorSel = `[data-name="${ancestor.getAttribute("data-name")}"]`;
      } else if (ancestor.className && typeof ancestor.className === "string" && ancestor.className.length > 2 && ancestor.className.length < 80) {
        // Use class-based selector only if it matches exactly one element on the page
        const cls = ancestor.className.split(/\s+/).filter(c => c.length > 2).join(".");
        if (cls && document.querySelectorAll("." + cls.split(".")[0]).length <= 3) {
          anchorSel = `${ancestor.tagName.toLowerCase()}.${cls}`;
        }
      }
      if (anchorSel) {
        // Find the element relative to this anchor
        const role = el.getAttribute("role");
        const ariaLabel = el.getAttribute("aria-label");
        if (role) {
          const matches = ancestor.querySelectorAll(`[role="${role}"]`);
          if (matches.length === 1) {
            selector = `${anchorSel} [role="${role}"]`;
          } else {
            const idx = Array.from(matches).indexOf(el);
            selector = `${anchorSel} [role="${role}"]:nth-of-type(${idx + 1})`;
          }
        } else if (ariaLabel) {
          selector = `${anchorSel} [aria-label="${ariaLabel}"]`;
        } else {
          const matches = ancestor.querySelectorAll(tag);
          const idx = Array.from(matches).indexOf(el);
          selector = `${anchorSel} ${tag}:nth-of-type(${idx + 1})`;
        }
        built = true;
        break;
      }
    }
    if (!built) {
      const parent = el.parentElement;
      if (parent) {
        const siblings = Array.from(parent.querySelectorAll(tag));
        selector = `${tag}:nth-of-type(${siblings.indexOf(el) + 1})`;
      }
    }
  }

  return {
    selector,
    input_type: inputType,
    label: label.substring(0, 200),
    required: el.required || el.getAttribute("aria-required") === "true",
    current_value: el.value || el.textContent || "",
    options,
    attributes: {
      name: el.name || "",
      id: el.id || "",
      placeholder: el.placeholder || "",
      "aria-label": el.getAttribute("aria-label") || "",
    },
    in_shadow_dom: false,
    in_iframe: iframeIndex !== null && iframeIndex !== undefined,
    iframe_index: iframeIndex,
    // Dynamic DOM context — exhaustive surrounding text for the LLM
    dom_context: domContext.context,
    label_sources: domContext.sources,
    group_label: domContext.context.split(" | ")[1] || "", // Second-best label candidate
    group_selector: "",
    parent_text: (() => {
      const p = el.parentElement;
      return p ? p.textContent.trim().substring(0, 300) : "";
    })(),
    fieldset_legend: (() => {
      const fs = el.closest("fieldset");
      if (!fs) return "";
      const leg = fs.querySelector("legend");
      return leg ? leg.textContent.trim() : "";
    })(),
    help_text: (() => {
      // Collect everything that could be help text
      const parts = [];
      const describedBy = el.getAttribute("aria-describedby");
      if (describedBy) {
        describedBy.split(/\s+/).forEach(id => {
          const desc = document.getElementById(id);
          if (desc) parts.push(desc.textContent.trim());
        });
      }
      const next = el.nextElementSibling;
      if (next && !JP.dom.isFieldVisible(next)) {} // skip hidden
      else if (next && next.textContent.trim().length < 200 &&
               !["INPUT","SELECT","TEXTAREA","BUTTON"].includes(next.tagName)) {
        parts.push(next.textContent.trim());
      }
      return parts.join(" ").substring(0, 300);
    })(),
    error_text: (() => {
      const errId = el.getAttribute("aria-errormessage");
      if (errId) {
        const errEl = document.getElementById(errId);
        if (errEl) return errEl.textContent.trim();
      }
      const parent = el.closest(".form-group, .field-wrapper, .form-field, [data-test-form-element]");
      if (parent) {
        const errEl = parent.querySelector(".error, .invalid-feedback, [role='alert'], .field-error");
        if (errEl) return errEl.textContent.trim();
      }
      return "";
    })(),
    aria_describedby: el.getAttribute("aria-describedby") || "",
  };
}

window.JobPulse.scanners.fieldInfo = { extractFieldInfo };
