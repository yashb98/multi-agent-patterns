// extension/scanners/field_context.js — Extract label + surrounding context for any form field
//
// Depends on: core/dom.js (loaded first)
// Exports: window.JobPulse.scanners.fieldContext.extractFieldContext

window.JobPulse = window.JobPulse || {};
window.JobPulse.scanners = window.JobPulse.scanners || {};

/**
 * Scrape every available label signal around a form element.
 * Returns { label, context } where label is our best guess and context is
 * the full surrounding text for the LLM.
 */
function extractFieldContext(el) {
  const texts = [];       // All candidate label texts, ranked by proximity
  const contextParts = []; // Full surrounding context for the LLM

  // Helper: clean text
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  // Helper: is this an input element?
  const isInput = (e) => e && ["INPUT","SELECT","TEXTAREA","BUTTON"].includes(e.tagName);

  // ─── Explicit associations (highest confidence) ───

  // <label for="id">
  if (el.id) {
    const labelFor = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (labelFor) texts.push({ text: clean(labelFor.textContent), rank: 1, src: "label[for]" });
  }

  // Wrapping <label>
  const wrappingLabel = el.closest("label");
  if (wrappingLabel) {
    // Get label text excluding the input's own text
    const clone = wrappingLabel.cloneNode(true);
    clone.querySelectorAll("input,select,textarea").forEach(c => c.remove());
    const t = clean(clone.textContent);
    if (t) texts.push({ text: t, rank: 1, src: "wrapping-label" });
  }

  // aria-labelledby
  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    const parts = labelledBy.split(/\s+/).map(id => {
      const ref = document.getElementById(id);
      return ref ? clean(ref.textContent) : "";
    }).filter(Boolean);
    if (parts.length) texts.push({ text: parts.join(" "), rank: 1, src: "aria-labelledby" });
  }

  // aria-label
  const ariaLabel = el.getAttribute("aria-label");
  if (ariaLabel) texts.push({ text: clean(ariaLabel), rank: 2, src: "aria-label" });

  // aria-description / aria-describedby
  const describedBy = el.getAttribute("aria-describedby");
  if (describedBy) {
    const parts = describedBy.split(/\s+/).map(id => {
      const ref = document.getElementById(id);
      return ref ? clean(ref.textContent) : "";
    }).filter(Boolean);
    if (parts.length) contextParts.push("described-by: " + parts.join(" "));
  }

  // title attribute
  if (el.title) texts.push({ text: clean(el.title), rank: 3, src: "title" });

  // placeholder
  if (el.placeholder) texts.push({ text: clean(el.placeholder), rank: 4, src: "placeholder" });

  // data-* attributes that might contain labels
  for (const attr of el.attributes) {
    if (attr.name.startsWith("data-") && /label|name|field|title|desc|hint|question/i.test(attr.name)) {
      const v = clean(attr.value);
      if (v && v.length < 200) texts.push({ text: v, rank: 3, src: `attr:${attr.name}` });
    }
  }

  // ─── DOM proximity (walk outward from the element) ───

  // Previous siblings (immediate + up to 3 levels)
  const collectPrevSiblings = (node, maxDepth) => {
    for (let depth = 0; node && depth < maxDepth; depth++) {
      let prev = node.previousElementSibling;
      while (prev) {
        if (!isInput(prev)) {
          const t = clean(prev.textContent);
          if (t.length > 0 && t.length < 200) {
            texts.push({ text: t, rank: 5 + depth, src: `prev-sib-d${depth}` });
            break; // Take the closest one at this depth
          }
        }
        prev = prev.previousElementSibling;
      }
      node = node.parentElement;
    }
  };
  collectPrevSiblings(el, 4);

  // Walk up ancestors, collecting container text
  let ancestor = el.parentElement;
  for (let depth = 0; ancestor && depth < 6; depth++, ancestor = ancestor.parentElement) {
    // Direct text nodes of this ancestor (not from child elements)
    const directText = Array.from(ancestor.childNodes)
      .filter(n => n.nodeType === 3)
      .map(n => clean(n.textContent))
      .filter(t => t.length > 0 && t.length < 150)
      .join(" ");
    if (directText) texts.push({ text: directText, rank: 7 + depth, src: `parent-text-d${depth}` });

    // Short text children of this ancestor that precede our element
    for (const child of ancestor.children) {
      if (child === el || child.contains(el)) break; // Stop at our element
      if (isInput(child)) continue;
      const t = clean(child.textContent);
      if (t.length > 0 && t.length < 150 && !child.querySelector("input,select,textarea")) {
        texts.push({ text: t, rank: 6 + depth, src: `ancestor-child-d${depth}` });
      }
    }

    // Check for legend, header, or label-like elements in this container
    const labelLike = ancestor.querySelector(
      ":scope > label, :scope > legend, :scope > h1, :scope > h2, :scope > h3, " +
      ":scope > h4, :scope > h5, :scope > h6, :scope > [class*='label'], " +
      ":scope > [class*='title'], :scope > [class*='header'], :scope > [class*='question']"
    );
    if (labelLike && !labelLike.contains(el) && !isInput(labelLike)) {
      const t = clean(labelLike.textContent);
      if (t.length > 0 && t.length < 200) {
        texts.push({ text: t, rank: 4 + depth, src: `label-like-d${depth}` });
      }
    }

    // Stop walking up if we hit a form, dialog, or major container
    const tag = ancestor.tagName.toLowerCase();
    if (["form", "dialog", "main", "body", "html"].includes(tag)) break;
  }

  // ─── Sibling fields context (what's around this field) ───
  // Next sibling text (sometimes labels come after)
  let nextSib = el.nextElementSibling;
  if (!nextSib && el.parentElement) nextSib = el.parentElement.nextElementSibling;
  if (nextSib && !isInput(nextSib)) {
    const t = clean(nextSib.textContent);
    if (t.length > 0 && t.length < 150) {
      texts.push({ text: t, rank: 10, src: "next-sib" });
    }
  }

  // ─── Build context string for the LLM ───
  // Deduplicate and sort by rank (lower = more likely to be the label)
  const seen = new Set();
  const unique = texts.filter(t => {
    if (seen.has(t.text)) return false;
    seen.add(t.text);
    return true;
  }).sort((a, b) => a.rank - b.rank);

  // Best label = highest ranked text
  const bestLabel = unique.length > 0 ? unique[0].text : "";

  // Full context = all unique texts joined (for the LLM to see everything)
  const fullContext = unique
    .map(t => t.text)
    .slice(0, 8) // Top 8 most relevant texts
    .join(" | ");

  return {
    label: bestLabel.substring(0, 200),
    context: fullContext.substring(0, 500),
    sources: unique.slice(0, 5).map(t => `${t.src}: "${t.text.substring(0, 60)}"`),
  };
}

window.JobPulse.scanners.fieldContext = { extractFieldContext };
