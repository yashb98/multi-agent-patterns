// extension/detectors/job_extract.js — Job card and JD text extraction for label strategy
window.JobPulse = window.JobPulse || {};
window.JobPulse.detectors = window.JobPulse.detectors || {};

/**
 * Extract job listing cards from search results pages.
 * Supports: Indeed, Greenhouse job boards, generic job listing pages.
 * Returns an array of {title, company, url, location, description} objects.
 */
function extractJobCards() {
  const hostname = window.location.hostname.toLowerCase();
  const cards = [];

  // ── Indeed ──────────────────────────────────────────────────────────────
  if (hostname.includes("indeed")) {
    const cardEls = document.querySelectorAll(
      ".job_seen_beacon, .resultContent, [data-jk], .jobsearch-ResultsList > li"
    );
    for (const card of cardEls) {
      const titleEl = card.querySelector("h2.jobTitle a, h2 a, .jobTitle a, a[data-jk]");
      const companyEl = card.querySelector(
        "[data-testid='company-name'], .companyName, .company, [data-company-name]"
      );
      const locationEl = card.querySelector(
        "[data-testid='text-location'], .companyLocation, .location"
      );
      const salaryEl = card.querySelector(
        ".salary-snippet-container, .estimated-salary, [data-testid='attribute_snippet_testid']"
      );
      const snippetEl = card.querySelector(".job-snippet, .underShelfFooter");

      const title = titleEl?.innerText?.trim() || "";
      const company = companyEl?.innerText?.trim() || "";
      let href = titleEl?.getAttribute("href") || "";
      if (href && !href.startsWith("http")) href = "https://uk.indeed.com" + href;

      if (!title || !href) continue;

      cards.push({
        title,
        company,
        url: href,
        location: locationEl?.innerText?.trim() || "",
        salary: salaryEl?.innerText?.trim() || "",
        description: snippetEl?.innerText?.trim() || "",
        platform: "indeed",
      });
    }
    return cards;
  }

  // ── Greenhouse job board ────────────────────────────────────────────────
  if (hostname.includes("greenhouse") || document.querySelector("#main .opening")) {
    const openings = document.querySelectorAll(".opening, [data-mapped='true'], .job-post");
    for (const el of openings) {
      const linkEl = el.querySelector("a");
      const locationEl = el.querySelector(".location, .job-post-location");

      const title = linkEl?.innerText?.trim() || "";
      let href = linkEl?.getAttribute("href") || "";
      if (href && !href.startsWith("http")) {
        href = window.location.origin + href;
      }

      if (!title || !href) continue;

      cards.push({
        title,
        company: document.querySelector(".company-name, h1")?.innerText?.trim() || "",
        url: href,
        location: locationEl?.innerText?.trim() || "",
        salary: "",
        description: "",
        platform: "greenhouse",
      });
    }
    return cards;
  }

  // ── Generic fallback — look for common job card patterns ────────────────
  const genericCards = document.querySelectorAll(
    "[class*='job-card'], [class*='job-listing'], [class*='vacancy'], [class*='search-result']"
  );
  for (const card of genericCards) {
    const linkEl = card.querySelector("a[href]");
    const title = linkEl?.innerText?.trim() || card.querySelector("h2, h3")?.innerText?.trim() || "";
    let href = linkEl?.getAttribute("href") || "";
    if (href && !href.startsWith("http")) {
      href = window.location.origin + href;
    }
    if (!title || !href) continue;

    cards.push({
      title,
      company: card.querySelector("[class*='company']")?.innerText?.trim() || "",
      url: href,
      location: card.querySelector("[class*='location']")?.innerText?.trim() || "",
      salary: card.querySelector("[class*='salary']")?.innerText?.trim() || "",
      description: "",
      platform: "generic",
    });
  }
  return cards;
}

/**
 * Extract full job description text from the current page.
 * Tries platform-specific selectors first, falls back to body text.
 */
function extractJDText() {
  const selectors = [
    // LinkedIn
    ".description__text", ".show-more-less-html__markup", "#job-details",
    // Indeed
    "#jobDescriptionText", ".jobsearch-jobDescriptionText",
    // Greenhouse
    "#content .body", ".job__description",
    // Lever
    ".posting-page .content", '[data-qa="job-description"]',
    // Workday
    '[data-automation-id="jobPostingDescription"]',
    // Generic
    "article", '[class*="description"]', '[class*="job-detail"]',
  ];

  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el) {
      const text = el.innerText?.trim();
      if (text && text.length > 100) {
        return text.replace(/\s+/g, " ").substring(0, 10000);
      }
    }
  }

  // Fallback: body text (limited)
  return (document.body?.innerText || "").replace(/\s+/g, " ").substring(0, 10000);
}

window.JobPulse.detectors.jobExtract = {
  extractJobCards,
  extractJDText,
};
