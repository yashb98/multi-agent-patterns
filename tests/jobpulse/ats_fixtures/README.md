# ATS Test Fixtures

This directory contains recorded snapshots of real ATS forms for CI replay.

## Recording a new fixture

```bash
python -m pytest tests/jobpulse/test_harness.py::record_greenhouse \
    --record-url "https://boards.greenhouse.io/stripe/jobs/123"
```

The fixture will be saved to `tests/jobpulse/ats_fixtures/<platform>_<url_slug>/`.

## Replaying in CI

```bash
python -m pytest tests/jobpulse/ats_fixtures/ -v --replay
```

## Fixture structure

```
greenhouse_boards.greenhouse.io_stripe_jobs_123/
├── manifest.json          # URL, platform, step list
├── step_00_initial.json   # Page snapshot after navigation
├── step_01_after_apply.json  # Page snapshot after clicking Apply
└── step_02_fields.json    # UnifiedFieldScanner output
```
