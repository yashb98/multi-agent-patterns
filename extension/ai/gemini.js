// extension/ai/gemini.js
// Gemini Nano (Chrome on-device AI) helpers for local field analysis.
// Extracted from content.js — Phase 5, Task 20.

window.JobPulse = window.JobPulse || {};

/**
 * Use Chrome's Prompt API (Gemini Nano) to analyze a form field locally.
 * Returns the answer string, or null if Nano is unavailable.
 */
async function analyzeFieldLocally(question, inputType, options) {
  if (!self.ai || !self.ai.languageModel) return null;

  try {
    const capabilities = await self.ai.languageModel.capabilities();
    if (capabilities.available === "no") return null;

    const session = await self.ai.languageModel.create({
      systemPrompt:
        "You fill job application forms for an ML Engineer with 2 years experience in the UK. " +
        "Return only the answer value, nothing else. No explanation, no quotes.",
    });

    let prompt = `Field: "${question}" (${inputType})`;
    if (options && options.length > 0) prompt += `\nOptions: ${options.join(", ")}`;
    prompt += "\nAnswer:";

    const answer = await session.prompt(prompt);
    session.destroy();
    return answer ? answer.trim() : null;
  } catch (e) {
    console.log("[JobPulse] Gemini Nano unavailable:", e.message);
    return null;
  }
}

/**
 * Use Chrome's Writer API for longer-form answers (textarea fields).
 * Falls back from Prompt API for questions needing paragraph answers.
 */
async function writeShortAnswer(question) {
  if (!self.ai || !self.ai.writer) return null;

  try {
    const capabilities = await self.ai.writer.capabilities();
    if (capabilities.available === "no") return null;

    const writer = await self.ai.writer.create({
      tone: "formal",
      length: "short",
      sharedContext: "Job application for ML Engineer position in the UK.",
    });
    const answer = await writer.write(question);
    writer.destroy();
    return answer ? answer.trim() : null;
  } catch (e) {
    console.log("[JobPulse] Writer API unavailable:", e.message);
    return null;
  }
}

window.JobPulse.ai = { analyzeFieldLocally, writeShortAnswer };
