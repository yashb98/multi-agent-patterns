# Multi-ATS URL Coverage Matrix

**Date**: 2026-05-10
**Source**: user-curated, 26 URLs across 11 ATS adapters.
**Used by**: `.claude/skills/audit-semantic-analysis/SKILL.md → Multi-ATS Coverage Matrix rule (rule 2)`. A slice cannot be declared done until every URL listed below produces evidence + correctness for every applicable dimension.

## URLs by ATS adapter

### Greenhouse (4 URLs)

- https://job-boards.greenhouse.io/anthropic/jobs/4017331008  (Anthropic — covered by live-e2e session)
- https://ohme-ev.com/jobs/ai-engineer-4862688101/?gh_jid=4862688101
- https://job-boards.greenhouse.io/graphcore/jobs/8539033002?gh_src=my.greenhouse.search
- https://job-boards.greenhouse.io/drweng/jobs/6586048?gh_src=c25a55fb1us

### Lever (3 URLs)

- https://jobs.lever.co/binance/f664ce6d-6fe8-4a7d-b2a3-1fa4d22dcd42
- https://jobs.lever.co/mistral/77b8339f-da37-4f38-b554-1d154f72ca8f
- https://jobs.lever.co/palantir/ff1029bd-bb6d-4d78-a03e-5f9744d0b798

### Workday (2 URLs)

- https://gresearch.wd103.myworkdayjobs.com/G-Research/job/London-UK/AI-Engineer_R3544/apply?source=linkedin
- https://accenture.wd103.myworkdayjobs.com/en-GB/AvanadeCareers/job/London/AI-Engineer_R00314813/apply

### Ashby (2 URLs)

- https://jobs.ashbyhq.com/openai/fc5bbc77-a30c-4e7a-9acc-8a2e748545b4
- https://jobs.ashbyhq.com/perplexity/79a07e2d-6150-4929-80fe-bbe13a641763

### LinkedIn Easy Apply (2 URLs)

- https://www.linkedin.com/jobs/view/4409696246/  (long tracking params stripped)
- https://www.linkedin.com/jobs/view/4402524171/  (long tracking params stripped)

### Indeed (2 URLs — note: Indeed often redirects to direct ATS)

- https://uk.indeed.com/?advn=9009361445701000&vjk=c2d6c75667306490
- https://uk.indeed.com/?vjk=6b6a038e962b2cdb&advn=8249818880948809  (resolves to → https://www.careers-inhealth.com/vacancies/5575/lead-data-analyst.html?source=Indeed which is Generic ATS)

### Reed (1 URL)

- https://www.reed.co.uk/jobs/data-scientist/56844592?source=signedinhome.recommendedjobs

### SmartRecruiters (2 URLs — covers shadow DOM `spl-*`)

- https://jobs.smartrecruiters.com/BoschGroup/744000125446259-data-scientist-ps-rbcd
- https://jobs.smartrecruiters.com/JobsForHumanity/744000125420010-associate-data-scientist-new-college-grad-2026

### iCIMS (2 URLs — covers iframe-based forms)

- https://careers.icims.com/careers-home/jobs/6309?lang=en-gb&previousLocale=en-US
- https://careers.icims.com/careers-home/jobs/6306?lang=en-gb&previousLocale=en-US

### Generic / unknown ATS (5 URLs — exercises the fallback path)

- https://footballradar.hire.trakstar.com/jobs/fk0zxy5  (Trakstar)
- https://sky.talent-community.com/projects/ai-engineer-(google)/62797  (Talent-community / pipeline-style ATS)
- https://jobs.dayforcehcm.com/en-GB/jdemea/CANDIDATEPORTAL/jobs/31168?scr=LinkedIn  (Dayforce HCM)
- https://career.mlp.com/careers/job/755953406323?domain=mlp.com  (MLP custom careers portal)
- https://www.avanade.com/en/career/job-details/R00314813?source=LinkedIn  (Avanade direct — but resolves to Workday; tests Generic→Workday handoff)

### Oracle Cloud HCM (1 URL — new ATS not in current adapter list)

- https://eoja.fa.ap1.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/5222?utm_medium=jobshare

> **Note**: Oracle Cloud HCM is **not in `jobpulse/ats_adapters/`** as a registered platform adapter — it falls through to Generic. Audit should flag this as a coverage gap and either (a) add an `oracle_cloud.py` adapter following the existing pattern, or (b) verify Generic handles it correctly. Either way it must be covered by a Slice.

## Coverage tally

| Adapter | URLs | Notes |
|---|---|---|
| Greenhouse | 4 | High-traffic; live-e2e covers Anthropic |
| Lever | 3 | High-traffic |
| Workday | 2 | DOM-heavy; React-controlled inputs |
| Ashby | 2 | Modern ATS; click-label radios pattern |
| LinkedIn (Easy Apply) | 2 | Auth-walled; tests SSO + Easy Apply badge detection |
| Indeed | 2 | Often redirects; tests Indeed→ATS handoff |
| Reed | 1 | Modal CV upload pattern |
| SmartRecruiters | 2 | Shadow DOM `spl-*` web components |
| iCIMS | 2 | Iframe-based forms; hCaptcha on login |
| Generic | 5 | Fallback path — multiple non-standard portals (Trakstar, Talent-community, Dayforce, MLP, Avanade→Workday redirect) |
| Oracle Cloud HCM | 1 | **NEW — no adapter exists; must add or verify Generic handles** |
| **Total** | **26** | Across 11 distinct ATS / portal types |

## Guidance for execution

- **Cross-URL correctness rule**: pick at least two URLs from different ATS where the JD context differs materially (e.g. one UK Greenhouse + one US Lever) and verify Profile-Driven decisions produce *different* answers when context warrants (visa-sponsorship, salary, location-aware fields).
- **Auth handling**: LinkedIn URLs require Easy Apply detection + SSO chain; iCIMS URLs require careful login handling. If auth fails, that's a P1 finding, not a skipped URL.
- **Indeed redirects**: when Indeed routes to a Generic-style ATS (in-health.com), audit must validate the *handoff* from Indeed scraping → direct ATS application.
- **Oracle Cloud**: new ATS — discovery, not just verification. Slice must include "add `oracle_cloud.py` adapter or document why Generic suffices".
- **Avanade Generic→Workday**: validates that Generic adapter correctly defers to Workday when the resolved URL is a Workday board.
