// extension/config.js — Single source of truth for all extension configuration

export const SEARCH_TITLES = [
  "data scientist",
  "data analyst",
  "data engineer",
  "machine learning engineer",
  "ai engineer",
  "software engineer",
];

export const SEARCH_FILTERS = {
  location: ["United Kingdom", "Scotland, United Kingdom"],
  experience: ["intern", "entry_level"],
  date_posted: "last_24_hours",
  sort: "most_recent",
};

export const ROLE_KEYWORDS = [
  "data scientist", "data analyst", "data engineer",
  "ml engineer", "machine learning engineer",
  "ai engineer", "artificial intelligence",
  "nlp engineer", "natural language processing",
  "software engineer",
  "devops",
  "frontend engineer", "front-end engineer", "frontend developer",
  "cloud engineer",
];

export const EXCLUDE_KEYWORDS = [
  // seniority
  "senior", "lead", "principal", "staff", "director",
  "manager", "head of", "vp", "architect", "chief",
  "10+ years", "8+ years", "5+ years", "3+ years",
  // wrong domain
  "ios", "android", "java developer", "php",
  "salesforce", "sap", "mainframe", ".net", "ruby", "golang",
  "embedded", "firmware", "hardware", "network engineer",
  "security engineer", "site reliability",
  "mechanical", "electrical", "civil", "chemical",
  "nurse", "doctor", "clinical", "pharmaceutical",
  "accounting", "finance analyst", "audit",
  "legal", "compliance officer", "solicitor",
  "teaching", "lecturer", "professor",
  "marketing manager", "content writer", "seo",
  "recruitment", "talent acquisition",
  "warehouse", "forklift", "driver",
  // wrong level
  "consultant", "contractor", "freelance",
  // wrong type
  "unpaid", "volunteer", "training contract",
  "apprenticeship level 2",
];

export const SCAN_SCHEDULE = {
  reed:       { times: ["09:00", "14:00"], days: "all week" },
  linkedin:   { times: ["10:00", "16:00"], days: "all week" },
  indeed:     { times: ["11:00"],          days: "all week" },
  greenhouse: { times: ["09:30", "15:00"], days: "all week" },
  glassdoor:  { times: ["13:00", "17:00"], days: "all week" },
};

export const SCAN_RATE_LIMITS = {
  reed:       { max_requests: 100, max_jobs: 100 },
  linkedin:   { max_requests: 80,  max_jobs: 80 },
  indeed:     { max_requests: 40,  max_jobs: 40 },
  greenhouse: { max_requests: 60,  max_jobs: 60 },
  glassdoor:  { max_requests: 20,  max_jobs: 20 },
};

export const APPLY_RATE_LIMITS = {
  linkedin:   10,
  indeed:     8,
  greenhouse: 7,
  lever:      7,
  workday:    5,
  glassdoor:  5,
  reed:       7,
  generic:    5,
};

export const PLATFORM_MAX_PHASE = {
  linkedin:   "supervised",
  indeed:     "supervised",
  workday:    "supervised",
  glassdoor:  "supervised",
  reed:       "auto",
  greenhouse: "auto",
  lever:      "auto",
  generic:    "supervised",
};

export const GRADUATION_THRESHOLDS = {
  observation_to_dry_run: 20,
  dry_run_to_supervised:  15,
  supervised_to_auto:     10,
};

export const BACKEND_URL = "http://localhost:8000";
export const NATIVE_HOST_NAME = "com.jobpulse.brain";

export function shouldOpenJob(title) {
  const lower = title.toLowerCase();
  const matchesRole = ROLE_KEYWORDS.some(k => lower.includes(k));
  const excluded = EXCLUDE_KEYWORDS.some(k => lower.includes(k));
  return matchesRole && !excluded;
}
