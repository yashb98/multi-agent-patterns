// extension/fillers/fill_radio.js — Label-aware radio group selection
// Changes when: radio group detection or selection changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

async function fillRadioGroup(groupSelector, value) {
  const JP = window.JobPulse;
  let radios;
  const container = JP.dom.resolveSelector(groupSelector);
  if (container && container.tagName.toLowerCase() !== "input") {
    radios = container.querySelectorAll("input[type='radio']");
  } else {
    const nameEl = JP.dom.resolveSelector(groupSelector);
    if (nameEl) {
      const name = nameEl.getAttribute("name");
      radios = name
        ? document.querySelectorAll(`input[type='radio'][name='${name}']`)
        : [nameEl];
    } else {
      return { success: false, error: "Radio group not found: " + groupSelector };
    }
  }

  if (!radios || radios.length === 0) {
    return { success: false, error: "No radio buttons found in: " + groupSelector };
  }

  const labelMap = [];
  for (const radio of radios) {
    let labelText = "";
    let labelEl = null;

    const radioId = radio.id;
    if (radioId) {
      labelEl = document.querySelector(`label[for='${radioId}']`);
      if (labelEl) labelText = labelEl.textContent.trim();
    }

    if (!labelText) {
      labelEl = radio.closest("label");
      if (labelEl) labelText = labelEl.textContent.trim();
    }

    if (!labelText) {
      labelText = radio.getAttribute("aria-label") || "";
    }

    if (!labelText && radio.nextSibling) {
      labelText = (radio.nextSibling.textContent || "").trim();
    }

    if (!labelText && radio.parentElement) {
      labelText = radio.parentElement.textContent.trim();
    }

    if (labelText) {
      labelMap.push({ text: labelText, radio, labelEl });
    }
  }

  if (labelMap.length === 0) {
    return { success: false, error: "No labels found for radio buttons" };
  }

  const labels = labelMap.map(l => l.text);
  const match = JP.form.fuzzyMatchOption(value, labels);

  if (!match) {
    return {
      success: false,
      error: `No matching radio for '${value}' in [${labels.slice(0, 5).join(", ")}]`,
    };
  }

  const matched = labelMap.find(l => l.text === match);
  if (matched) {
    const target = matched.labelEl || matched.radio;
    await JP.dom.smartScroll(target);
    await JP.dom.delay(JP.timing.getFieldGap(match));
    target.click();
    matched.radio.dispatchEvent(new Event("change", { bubbles: true }));
    return { success: true, value_set: match, value_verified: matched.radio.checked };
  }

  return { success: false, error: "Match found but click failed" };
}

window.JobPulse.fillers.radio = { fillRadioGroup };
