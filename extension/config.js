/**
 * Chrome MV3 Extension Configuration
 * Single source of truth for job automation extension settings
 */

// Job titles to search for across platforms
export const SEARCH_TITLES = [
  'data scientist',
  'data analyst',
  'data engineer',
  'machine learning engineer',
  'ai engineer',
  'software engineer'
];

// Platform-specific filters for job searches
export const SEARCH_FILTERS = {
  location: ['United Kingdom', 'Scotland, United Kingdom'],
  experience: ['intern', 'entry_level'],
  date_posted: 'last_24_hours',
  sort: 'most_recent'
};

// Keywords for Gate 0 title pre-filter (include these roles)
export const ROLE_KEYWORDS = [
  'data scientist',
  'data analyst',
  'data engineer',
  'ml engineer',
  'machine learning engineer',
  'ai engineer',
  'artificial intelligence',
  'nlp engineer',
  'natural language processing',
  'software engineer',
  'devops',
  'frontend engineer',
  'front-end engineer',
  'frontend developer',
  'cloud engineer'
];

// Keywords that exclude jobs from consideration
export const EXCLUDE_KEYWORDS = {
  // Seniority levels (too senior)
  seniority: [
    'senior',
    'lead',
    'principal',
    'staff',
    'director',
    'manager',
    'head of',
    'vp',
    'architect',
    'chief',
    '10+ years',
    '8+ years',
    '5+ years',
    '3+ years'
  ],
  // Wrong technical domain
  wrongDomain: [
    'ios',
    'android',
    'java developer',
    'php',
    'salesforce',
    'sap',
    'mainframe',
    '.net',
    'ruby',
    'golang',
    'embedded',
    'firmware',
    'hardware',
    'network engineer',
    'security engineer',
    'site reliability',
    'mechanical',
    'electrical',
    'civil',
    'chemical',
    'nurse',
    'doctor',
    'clinical',
    'pharmaceutical',
    'accounting',
    'finance analyst',
    'audit',
    'legal',
    'compliance officer',
    'solicitor',
    'teaching',
    'lecturer',
    'professor',
    'marketing manager',
    'content writer',
    'seo',
    'recruitment',
    'talent acquisition',
    'warehouse',
    'forklift',
    'driver'
  ],
  // Wrong employment type
  wrongLevel: [
    'consultant',
    'contractor',
    'freelance'
  ],
  // Wrong work arrangement
  wrongType: [
    'unpaid',
    'volunteer',
    'training contract',
    'apprenticeship level 2'
  ]
};

/**
 * Gate 0 title pre-filter
 * Returns true if job title contains any ROLE_KEYWORDS and no EXCLUDE_KEYWORDS
 *
 * @param {string} title - Job title to evaluate
 * @returns {boolean} - True if job passes Gate 0 filter
 */
export function shouldOpenJob(title) {
  if (!title || typeof title !== 'string') {
    return false;
  }

  const lowerTitle = title.toLowerCase();

  // Check for at least one include keyword
  const hasRoleKeyword = ROLE_KEYWORDS.some(keyword =>
    lowerTitle.includes(keyword.toLowerCase())
  );

  if (!hasRoleKeyword) {
    return false;
  }

  // Check for any exclude keywords
  const allExcludeKeywords = [
    ...EXCLUDE_KEYWORDS.seniority,
    ...EXCLUDE_KEYWORDS.wrongDomain,
    ...EXCLUDE_KEYWORDS.wrongLevel,
    ...EXCLUDE_KEYWORDS.wrongType
  ];

  const hasExcludeKeyword = allExcludeKeywords.some(keyword =>
    lowerTitle.includes(keyword.toLowerCase())
  );

  return !hasExcludeKeyword;
}

// Platform-specific scan schedules (24-hour format)
export const SCAN_SCHEDULE = {
  reed: ['09:00', '14:00'],
  linkedin: ['10:00', '16:00'],
  indeed: ['11:00'],
  greenhouse: ['09:30', '15:00'],
  glassdoor: ['13:00', '17:00']
};

// Rate limits per platform (scan + view operations per day)
export const SCAN_RATE_LIMITS = {
  reed: { scan: 100, view: 100 },
  linkedin: { scan: 80, view: 80 },
  indeed: { scan: 40, view: 40 },
  greenhouse: { scan: 60, view: 60 },
  glassdoor: { scan: 20, view: 20 }
};

// Maximum automation phase per platform
// Phases: observation -> dry_run -> supervised -> auto
export const PLATFORM_MAX_PHASE = {
  linkedin: 'supervised',
  indeed: 'supervised',
  workday: 'supervised',
  glassdoor: 'supervised',
  reed: 'auto',
  greenhouse: 'auto',
  lever: 'auto',
  generic: 'supervised'
};

// Graduation thresholds for advancing through automation phases
export const GRADUATION_THRESHOLDS = {
  // Observation phase -> Dry run phase
  observation_to_dry_run: {
    correct_mappings: 20,      // Min correct field mappings observed
    max_unknown_fields: 0,     // Max unknown fields allowed
    psi_threshold: 0.1         // Population stability index threshold
  },
  // Dry run phase -> Supervised phase
  dry_run_to_supervised: {
    clean_runs: 15,            // Clean dry runs required
    accuracy: 0.95,            // Field detection accuracy required
    field_detection: 0.98,     // Field detection confidence
    captcha_triggers: 0        // Max CAPTCHA triggers allowed
  },
  // Supervised phase -> Auto phase
  supervised_to_auto: {
    unmodified_approvals: 10,  // Unmodified approvals required
    submission_errors: 0,      // Max submission errors allowed
    rejection_24h: 0,          // Max rejections in 24h
    psi_threshold: 0.2         // Population stability index threshold
  }
};

// Daily application limits per platform (from rules: daily rate limits updated 2026-03-31)
export const DAILY_APPLY_LIMITS = {
  linkedin: 15,
  greenhouse: 7,
  lever: 7,
  indeed: 8,
  workday: 8,
  reed: 7,
  generic: 5
};

// Backend API configuration
export const BACKEND_URL = 'http://localhost:8000';

// API endpoints for backend communication
export const API_ENDPOINTS = {
  evaluate: '/api/job/evaluate',
  evaluate_batch: '/api/job/evaluate-batch',
  generate_cv: '/api/job/generate-cv',
  scan_reed: '/api/job/scan-reed',
  scan_linkedin: '/api/job/scan-linkedin',
  ralph_learn: '/api/job/ralph-learn',
  notify: '/api/job/notify',
  health: '/api/job/health'
};

/**
 * Build full API URL from endpoint
 *
 * @param {string} endpoint - API endpoint path
 * @returns {string} - Full API URL
 */
export function buildApiUrl(endpoint) {
  if (!API_ENDPOINTS[endpoint]) {
    throw new Error(`Unknown API endpoint: ${endpoint}`);
  }
  return BACKEND_URL + API_ENDPOINTS[endpoint];
}
