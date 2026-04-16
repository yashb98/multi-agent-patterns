"""Two-phase form analyzer: deterministic fill + LLM for remaining fields.

Phase 1 (Deterministic): Pattern-match labels → instant, free, ~60% of fields
Phase 2 (LLM):           Remaining fields with real dropdown options → ~$0.003

Profile data is hardcoded. Everything else is autonomous.
"""

from __future__ import annotations

import json
import re
from typing import Any

from shared.agents import get_openai_client
from shared.logging_config import get_logger

from jobpulse.applicator import PROFILE, WORK_AUTH
from jobpulse.ext_models import Action, FieldInfo, PageSnapshot
from jobpulse.screening_answers import ROLE_SALARY, SKILL_EXPERIENCE

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Placeholder values to treat as empty
# ---------------------------------------------------------------------------
_PLACEHOLDER_VALUES = {"-none-", "loading", "-none- loading", "select", "select...", "choose", "please select"}

# ---------------------------------------------------------------------------
# Profile context for LLM prompt
# ---------------------------------------------------------------------------

_PROFILE_CONTEXT = f"""## Applicant Profile
- Name: {PROFILE['first_name']} {PROFILE['last_name']}
- Email: {PROFILE['email']}
- Phone: {PROFILE['phone']}
- Location: {PROFILE['location']}
- Education: {PROFILE['education']}
- LinkedIn: {PROFILE['linkedin']}
- GitHub: {PROFILE['github']}
- Portfolio: {PROFILE.get('portfolio', '')}

## Work Authorization (UK)
- Visa: {WORK_AUTH['visa_status']}
- Right to work in UK: Yes (Graduate Visa from May 2026, valid 2 years)
- Requires sponsorship: No
- Notice period: Immediately
- Current employer: Co-op (Team Leader)
- Current salary: £22,000

## Demographics (for equality monitoring forms)
- Gender: Male | Pronouns: He/Him
- Ethnicity: Asian or Asian British - Indian
- Nationality: Indian
- Religion: Hindu
- Disability: No
- Veteran: No
- Age range: 25-29
- Marital status: Single
- Driving licence: Yes

## Skills & Experience
- Python: 3 years | SQL: 3 years
- Machine Learning, Deep Learning, NLP, LLMs, Gen AI: 2 years
- TensorFlow, PyTorch, scikit-learn, pandas, numpy: 2 years
- Docker, Git, Linux, AWS, CI/CD: 2 years
- FastAPI, Flask, REST APIs: 2 years
- Spark, Airflow, ETL pipelines: 2 years
- Tableau, Power BI: 2 years
- React, JavaScript, TypeScript: 2 years
- Team management: 3 years (managed team of 8)

## Salary Expectations (by role)
- Data Scientist / ML Engineer / AI Engineer: £32,000
- Data Analyst: £28,000
- Data Engineer / Software Engineer: £30,000

## Standard Answers
- Willing to relocate within UK: Yes
- Willing to work remote/hybrid/on-site: Yes
- Background check: Yes, willing to undergo
- Security clearance: None currently held
- Languages: English (Native), Hindi (Native)
- Full-time preferred
- Available to start: Immediately
"""

# ---------------------------------------------------------------------------
# Phase 1: Deterministic fill — pattern matching, instant, free
# ---------------------------------------------------------------------------

# Each entry: (label_regex, value, action_type)
# Order matters — first match wins. More specific patterns first.
# 230+ rules across 40 categories — covers Greenhouse, Lever, Workday,
# ZohoRecruit, SmartRecruiters, iCIMS, Taleo, BambooHR, LinkedIn, Indeed, etc.
_DETERMINISTIC_RULES: list[tuple[str, str, str]] = [
    # ── FIRST NAME / GIVEN NAME (13 patterns) ──
    (r"passport[\s_-]*first[\s_-]*name", PROFILE["first_name"], "fill"),
    (r"legal[\s_-]*first[\s_-]*name", PROFILE["first_name"], "fill"),
    (r"candidate[\s_-]*first[\s_-]*name|applicant[\s_-]*first[\s_-]*name", PROFILE["first_name"], "fill"),
    (r"your[\s_-]*first[\s_-]*name|what[\s_-]*is[\s_-]*your[\s_-]*first[\s_-]*name", PROFILE["first_name"], "fill"),
    (r"preferred[\s_-]*first[\s_-]*name", PROFILE["first_name"], "fill"),
    (r"first[\s_-]*name|given[\s_-]*name|forename", PROFILE["first_name"], "fill"),
    (r"^fname$|^f_name$", PROFILE["first_name"], "fill"),

    # ── LAST NAME / SURNAME / FAMILY NAME (12 patterns) ──
    (r"passport[\s_-]*last[\s_-]*name", PROFILE["last_name"], "fill"),
    (r"legal[\s_-]*last[\s_-]*name", PROFILE["last_name"], "fill"),
    (r"candidate[\s_-]*last[\s_-]*name|applicant[\s_-]*last[\s_-]*name", PROFILE["last_name"], "fill"),
    (r"your[\s_-]*last[\s_-]*name|your[\s_-]*surname", PROFILE["last_name"], "fill"),
    (r"what[\s_-]*is[\s_-]*your[\s_-]*(last[\s_-]*name|surname)", PROFILE["last_name"], "fill"),
    (r"last[\s_-]*name|surname|family[\s_-]*name", PROFILE["last_name"], "fill"),
    (r"^lname$|^l_name$", PROFILE["last_name"], "fill"),

    # ── FULL NAME (6 patterns) ──
    (r"^full[\s_-]*name$|^your[\s_-]*name$", f"{PROFILE['first_name']} {PROFILE['last_name']}", "fill"),
    (r"^name$", f"{PROFILE['first_name']} {PROFILE['last_name']}", "fill"),
    (r"candidate[\s_-]*name|applicant[\s_-]*name|legal[\s_-]*name", f"{PROFILE['first_name']} {PROFILE['last_name']}", "fill"),

    # ── MIDDLE NAME ──
    (r"middle[\s_-]*name|middle[\s_-]*initial", "", "fill"),

    # ── PREFERRED NAME / NICKNAME (5 patterns) ──
    (r"known[\s_-]*as|preferred[\s_-]*name|nickname|display[\s_-]*name", PROFILE["first_name"], "fill"),
    (r"what.*(?:like|prefer).*(?:called|known)", PROFILE["first_name"], "fill"),

    # ── EMAIL (9 patterns) ──
    (r"confirm[\s_-]*email|verify[\s_-]*email|re[\s_-]*enter[\s_-]*email", PROFILE["email"], "fill"),
    (r"candidate[\s_-]*email|applicant[\s_-]*email|personal[\s_-]*email", PROFILE["email"], "fill"),
    (r"contact[\s_-]*email|your[\s_-]*email|what[\s_-]*is[\s_-]*your[\s_-]*email", PROFILE["email"], "fill"),
    (r"e[\s_-]*mail[\s_-]*address|email[\s_-]*id", PROFILE["email"], "fill"),
    (r"^e[\s_-]*mail$|^email$", PROFILE["email"], "fill"),

    # ── PHONE / TELEPHONE / MOBILE (11 patterns) ──
    (r"mobile[\s_-]*number|mobile[\s_-]*phone|cell[\s_-]*phone|cell[\s_-]*number", PROFILE["phone"], "fill"),
    (r"telephone[\s_-]*number|telephone|contact[\s_-]*number|contact[\s_-]*phone", PROFILE["phone"], "fill"),
    (r"your[\s_-]*phone|your[\s_-]*mobile|primary[\s_-]*phone|main[\s_-]*phone", PROFILE["phone"], "fill"),
    (r"home[\s_-]*phone|home[\s_-]*number|daytime[\s_-]*phone|daytime[\s_-]*number", PROFILE["phone"], "fill"),
    (r"what[\s_-]*is[\s_-]*your[\s_-]*(phone|mobile|contact[\s_-]*number)", PROFILE["phone"], "fill"),
    (r"phone[\s_-]*number|^phone$|^tel$|^mobile$", PROFILE["phone"], "fill"),

    # ── TITLE / SALUTATION / PREFIX (5 patterns) ──
    (r"^title$|^salutation$|^prefix$|^honorific$", "Mr", "fill_combobox"),
    (r"^mr\b.*ms\b|^mr\/mrs|title.*mr", "Mr", "fill_combobox"),
    (r"name[\s_-]*prefix|name[\s_-]*title", "Mr", "fill_combobox"),
    (r"how.*addressed|how.*like.*addressed", "Mr", "fill_combobox"),

    # ── LINKEDIN (4 patterns) ──
    (r"linkedin[\s_-]*(?:profile|url|link|page)", PROFILE["linkedin"], "fill"),
    (r"your[\s_-]*linkedin|link.*linkedin|url.*linkedin", PROFILE["linkedin"], "fill"),
    (r"^linkedin$", PROFILE["linkedin"], "fill"),

    # ── GITHUB (4 patterns) ──
    (r"github[\s_-]*(?:profile|url|link|username)", PROFILE["github"], "fill"),
    (r"your[\s_-]*github|link.*github|url.*github", PROFILE["github"], "fill"),
    (r"code[\s_-]*repository|code[\s_-]*samples", PROFILE["github"], "fill"),
    (r"^github$", PROFILE["github"], "fill"),

    # ── PORTFOLIO / WEBSITE (9 patterns) ──
    (r"portfolio[\s_-]*(?:url|link|website)", PROFILE.get("portfolio", ""), "fill"),
    (r"personal[\s_-]*website|personal[\s_-]*site", PROFILE.get("portfolio", ""), "fill"),
    (r"^website$|^website[\s_-]*url$|your[\s_-]*website", PROFILE.get("portfolio", ""), "fill"),
    (r"online[\s_-]*portfolio|link.*(?:portfolio|website|work)", PROFILE.get("portfolio", ""), "fill"),
    (r"kaggle[\s_-]*(?:profile|url)|blog[\s_-]*(?:url|link)", PROFILE.get("portfolio", ""), "fill"),
    (r"other[\s_-]*(?:url|link|website)", PROFILE.get("portfolio", ""), "fill"),
    (r"(?:personal[\s_-]*)?(?:blog|homepage)[\s_-]*(?:url|link)?", PROFILE.get("portfolio", ""), "fill"),
    (r"social[\s_-]*(?:media[\s_-]*)?(?:profile|link|url)|online[\s_-]*(?:profile|presence)", PROFILE["linkedin"], "fill"),

    # ── WORK AUTHORIZATION / RIGHT TO WORK (20 patterns) ──
    (r"right[\s_-]*to[\s_-]*work.*(?:uk|united[\s_-]*kingdom|britain)", "Yes", "fill_combobox"),
    (r"authoriz?ed.*work.*uk|work[\s_-]*authoriz?ation.*uk", "Yes", "fill_combobox"),
    (r"eligible.*work.*uk|eligib.*employment.*uk", "Yes", "fill_combobox"),
    (r"legally.*(?:entitled|allowed|permitted).*work", "Yes", "fill_combobox"),
    (r"do[\s_-]*you[\s_-]*have[\s_-]*(?:the[\s_-]*)?right[\s_-]*to[\s_-]*work", "Yes", "fill_combobox"),
    (r"are[\s_-]*you[\s_-]*(?:legally[\s_-]*)?(?:authoriz?ed|eligible|entitled)[\s_-]*to[\s_-]*work", "Yes", "fill_combobox"),
    (r"employment[\s_-]*eligib|employment[\s_-]*verif", "Yes", "fill_combobox"),
    (r"require.*(?:visa[\s_-]*)?sponsor|need.*sponsor|sponsorship.*require", "No", "fill_combobox"),
    (r"will[\s_-]*you.*require[\s_-]*sponsor|do[\s_-]*you.*need.*sponsor", "No", "fill_combobox"),
    (r"now[\s_-]*or[\s_-]*(?:in[\s_-]*the[\s_-]*)?future.*sponsor", "No", "fill_combobox"),
    (r"visa[\s_-]*status|immigration[\s_-]*status", "Graduate Visa", "fill_combobox"),
    (r"visa[\s_-]*type|type[\s_-]*of[\s_-]*visa|which[\s_-]*visa", "Graduate Visa", "fill"),
    (r"visa[\s_-]*expir|visa[\s_-]*end|current[\s_-]*status[\s_-]*conclude|status[\s_-]*end[\s_-]*date", "2028-05-09", "fill_date"),
    (r"document.*right.*work|evidence.*right.*work|proof.*right.*work", "Graduate Visa", "fill_combobox"),
    (r"work[\s_-]*permit|work[\s_-]*visa|work[\s_-]*authoriz?ation", "Graduate Visa", "fill_combobox"),
    (r"british[\s_-]*citizen|uk[\s_-]*citizen", "No", "fill_combobox"),
    (r"eu[\s_-]*national|eea[\s_-]*national", "No", "fill_combobox"),
    (r"indefinite[\s_-]*leave[\s_-]*to[\s_-]*remain|\bilr\b|settled[\s_-]*status|pre[\s_-]*settled", "No", "fill_combobox"),
    (r"subject.*immigration.*(?:control|restrict)", "No", "fill_combobox"),

    # ── AVAILABILITY / START DATE / NOTICE PERIOD (9 patterns) ──
    (r"availab.*start|available[\s_-]*from|when.*(?:start|available|begin)", "Immediately", "fill"),
    (r"notice[\s_-]*period|how[\s_-]*(?:much|long)[\s_-]*notice", "Immediately", "fill"),
    (r"how[\s_-]*soon[\s_-]*(?:can|could)[\s_-]*you[\s_-]*start", "Immediately", "fill"),
    (r"when[\s_-]*(?:could|can|would)[\s_-]*you[\s_-]*(?:start|begin|commence)", "Immediately", "fill"),
    (r"immediate[\s_-]*start|available[\s_-]*immediately", "Yes", "fill"),
    (r"^availability$|your[\s_-]*availability", "Immediately", "fill"),
    (r"current[\s_-]*notice[\s_-]*period", "None - available immediately", "fill"),
    (r"weeks?[\s_-]*notice|months?[\s_-]*notice", "0", "fill"),
    (r"date[\s_-]*available|available[\s_-]*date", "Immediately", "fill"),

    # ── SALARY EXPECTATIONS (7 patterns) ──
    (r"expected[\s_-]*salary|desired[\s_-]*salary|salary[\s_-]*expectation", "32000", "fill"),
    (r"minimum[\s_-]*salary|minimum[\s_-]*acceptable", "28000", "fill"),
    (r"current[\s_-]*salary|present[\s_-]*salary|existing[\s_-]*salary", "22000", "fill"),
    (r"current[\s_-]*(?:compensation|package|base[\s_-]*salary)", "22000", "fill"),
    (r"what[\s_-]*(?:is|was)[\s_-]*your[\s_-]*(?:current|last|previous)[\s_-]*salary", "22000", "fill"),
    (r"what[\s_-]*(?:are|is)[\s_-]*your[\s_-]*salary[\s_-]*expect", "32000", "fill"),
    (r"hourly[\s_-]*rate|daily[\s_-]*rate|day[\s_-]*rate", "150", "fill"),

    # ── DEMOGRAPHICS — GENDER (5 patterns) ──
    (r"^gender$|^sex$|what.*gender|gender.*identity", "Male", "fill_combobox"),
    (r"indicate.*gender|select.*gender|your[\s_-]*gender", "Male", "fill_combobox"),
    (r"how[\s_-]*do[\s_-]*you[\s_-]*(?:identify|describe)[\s_-]*your[\s_-]*gender", "Male", "fill_combobox"),
    (r"sex[\s_-]*recorded[\s_-]*at[\s_-]*birth|assigned[\s_-]*sex", "Male", "fill_combobox"),
    (r"gender.*same.*sex.*recorded|gender.*match.*sex.*birth", "Yes", "fill_combobox"),

    # ── DEMOGRAPHICS — ETHNICITY (4 patterns) ──
    (r"ethnic(?:ity)?|ethnic[\s_-]*(?:group|background|origin)", "Asian or Asian British - Indian", "fill_combobox"),
    (r"racial[\s_-]*(?:background|identity)|^race$", "Asian", "fill_combobox"),
    (r"indicate.*ethnic|select.*ethnic|your[\s_-]*ethnicity", "Asian or Asian British - Indian", "fill_combobox"),
    (r"what[\s_-]*(?:is|best[\s_-]*describes)[\s_-]*your[\s_-]*ethnic", "Asian or Asian British - Indian", "fill_combobox"),

    # ── DEMOGRAPHICS — DISABILITY (6 patterns) ──
    (r"disab(?:ility|led)|do[\s_-]*you[\s_-]*(?:have|consider).*disab", "No", "fill_combobox"),
    (r"long[\s_-]*term[\s_-]*(?:health|physical|mental)[\s_-]*condition", "No", "fill_combobox"),
    (r"equality[\s_-]*act[\s_-]*2010|disability.*(?:equality|discrimination)[\s_-]*act", "No", "fill_combobox"),
    (r"impairment.*health|health.*condition.*(?:12[\s_-]*months|long[\s_-]*term)", "No", "fill_combobox"),
    (r"confident.*disab|disability[\s_-]*confident|guaranteed[\s_-]*interview", "No", "fill_combobox"),
    (r"reasonable[\s_-]*adjust|workplace[\s_-]*adjust|special[\s_-]*accommod|access[\s_-]*require", "No", "fill_combobox"),

    # ── DEMOGRAPHICS — VETERAN / MILITARY (4 patterns) ──
    (r"veteran|military[\s_-]*(?:service|status)|armed[\s_-]*forces", "No", "fill_combobox"),
    (r"ex[\s_-]*(?:service|military|forces)|former[\s_-]*(?:military|armed)", "No", "fill_combobox"),
    (r"served[\s_-]*(?:in[\s_-]*)?(?:military|armed[\s_-]*forces|army|navy|raf)", "No", "fill_combobox"),
    (r"protected[\s_-]*veteran|veteran[\s_-]*status", "I am not a protected veteran", "fill_combobox"),

    # ── DEMOGRAPHICS — RELIGION (3 patterns) ──
    (r"religion|religious[\s_-]*(?:belief|faith)|belief[\s_-]*system", "Hindu", "fill_combobox"),
    (r"^faith$|spiritual[\s_-]*(?:belief|practice)", "Hindu", "fill_combobox"),
    (r"what[\s_-]*(?:is|best[\s_-]*describes)[\s_-]*your[\s_-]*(?:religion|faith|belief)", "Hindu", "fill_combobox"),

    # ── DEMOGRAPHICS — SEXUAL ORIENTATION (2 patterns) ──
    (r"sexual[\s_-]*orientation|what.*orientation", "Heterosexual/Straight", "fill_combobox"),
    (r"indicate.*orientation|your[\s_-]*sexual[\s_-]*orientation", "Heterosexual/Straight", "fill_combobox"),

    # ── DEMOGRAPHICS — MARITAL STATUS (2 patterns) ──
    (r"marital[\s_-]*status|civil[\s_-]*(?:status|partnership)", "Single", "fill_combobox"),
    (r"relationship[\s_-]*status", "Single", "fill_combobox"),

    # ── DEMOGRAPHICS — PRONOUNS (3 patterns) ──
    (r"pronoun|preferred[\s_-]*pronoun|your[\s_-]*pronoun", "He/Him", "fill_combobox"),
    (r"what[\s_-]*(?:are|is)[\s_-]*your[\s_-]*pronoun", "He/Him", "fill_combobox"),
    (r"indicate.*pronoun|select.*pronoun", "He/Him", "fill_combobox"),

    # ── DEMOGRAPHICS — AGE (4 patterns) ──
    (r"age[\s_-]*(?:group|range|bracket)|what.*your[\s_-]*age", "25-29", "fill_combobox"),
    (r"over[\s_-]*18|above[\s_-]*18|are[\s_-]*you[\s_-]*(?:at[\s_-]*least[\s_-]*)?18", "Yes", "fill_combobox"),
    (r"are[\s_-]*you[\s_-]*(?:over|above|at[\s_-]*least)[\s_-]*21", "Yes", "fill_combobox"),
    (r"confirm.*(?:legal|working)[\s_-]*age", "Yes", "fill_combobox"),

    # ── NATIONALITY / CITIZENSHIP (5 patterns) ──
    (r"nationality|what.*nationality", "Indian", "fill_combobox"),
    (r"country[\s_-]*of[\s_-]*(?:citizenship|birth|origin)", "India", "fill_combobox"),
    (r"citizen(?:ship)?[\s_-]*(?:status|country)?", "Indian", "fill_combobox"),
    (r"national[\s_-]*(?:origin|identity)", "Indian", "fill_combobox"),
    (r"passport[\s_-]*(?:country|nationality)", "India", "fill_combobox"),

    # ── LOCATION / ADDRESS (9 patterns) ──
    (r"^city$|current[\s_-]*city|city[\s_-]*of[\s_-]*residence", "Dundee", "fill"),
    (r"^town$|town[\s_-]*or[\s_-]*city|city[\s_-]*town", "Dundee", "fill"),
    (r"^country$|current[\s_-]*country|country[\s_-]*of[\s_-]*residence", "United Kingdom", "fill_combobox"),
    (r"^location$|current[\s_-]*location|your[\s_-]*location", PROFILE["location"], "fill"),
    (r"where[\s_-]*(?:are[\s_-]*you[\s_-]*)?(?:based|located|living)", PROFILE["location"], "fill"),
    (r"^address$|home[\s_-]*address|street[\s_-]*address|address[\s_-]*line", PROFILE["location"], "fill"),
    (r"post[\s_-]*code|postcode|postal[\s_-]*code|zip[\s_-]*code|^zip$", "DD1", "fill"),
    (r"^state$|^province$|^county$|^region$", "Scotland", "fill_combobox"),
    (r"country[\s_-]*region|state[\s_-]*province", "Scotland", "fill_combobox"),

    # ── RELOCATION / REMOTE WORK (6 patterns) ──
    (r"willing.*relocat|open.*relocation|consider.*relocation", "Yes", "fill_combobox"),
    (r"relocat.*within.*(?:uk|united[\s_-]*kingdom)", "Yes", "fill_combobox"),
    (r"remote[\s_-]*(?:work|position|role)|work[\s_-]*(?:from[\s_-]*home|remotely)", "Yes", "fill_combobox"),
    (r"hybrid[\s_-]*(?:work|arrange|model)|days.*(?:office|on[\s_-]*site)", "Yes", "fill_combobox"),
    (r"on[\s_-]*site|in[\s_-]*(?:office|person)", "Yes", "fill_combobox"),
    (r"comfortable.*(?:remote|office|hybrid)", "Yes", "fill_combobox"),

    # ── EDUCATION (9 patterns) ──
    (r"highest[\s_-]*(?:education|qualification|degree|level.*education)", "Master's Degree", "fill_combobox"),
    (r"level[\s_-]*of[\s_-]*education|education[\s_-]*level", "Master's Degree", "fill_combobox"),
    (r"what[\s_-]*(?:is|was)[\s_-]*your[\s_-]*highest[\s_-]*(?:degree|qualification)", "Master's Degree", "fill_combobox"),
    (r"degree[\s_-]*(?:type|name|classification)|type[\s_-]*of[\s_-]*degree", "MSc", "fill"),
    (r"field[\s_-]*of[\s_-]*study|course[\s_-]*(?:name|title)|^subject$|^major$|^discipline$", "Computer Science", "fill"),
    (r"degree[\s_-]*subject|what.*(?:study|major|degree[\s_-]*in)", "Computer Science", "fill"),
    (r"university|institution|school[\s_-]*name|college[\s_-]*name", "University of Dundee", "fill"),
    (r"currently[\s_-]*(?:enrolled|studying|student)|are[\s_-]*you.*student", "No", "fill"),
    (r"stem[\s_-]*degree|computer[\s_-]*science[\s_-]*degree|relevant[\s_-]*(?:degree|qualification)", "Yes", "fill"),

    # ── EXPERIENCE / EMPLOYMENT (3 patterns) ──
    (r"(?:current|previous)[\s_-]*(?:job[\s_-]*)?title|current[\s_-]*role|current[\s_-]*position", "Team Leader", "fill"),
    (r"(?:current|present)[\s_-]*employer|company.*work.*for|who.*work.*for", "Co-op", "fill"),
    (r"currently[\s_-]*employ|employment[\s_-]*status|are[\s_-]*you.*employ", "Yes", "fill"),

    # ── DRIVING LICENCE (7 patterns) ──
    (r"driv(?:ing)?[\s_-]*licen[cs]e|driver(?:'?s)?[\s_-]*licen[cs]e", "Yes", "fill_combobox"),
    (r"valid[\s_-]*(?:driving[\s_-]*)?licen[cs]e|full[\s_-]*(?:driving[\s_-]*)?licen[cs]e", "Yes", "fill_combobox"),
    (r"clean[\s_-]*(?:driving[\s_-]*)?licen[cs]e|points.*licen[cs]e", "Yes", "fill_combobox"),
    (r"(?:do[\s_-]*you[\s_-]*)?(?:have|hold)[\s_-]*a[\s_-]*(?:full|valid|clean)?[\s_-]*(?:uk[\s_-]*)?(?:driving[\s_-]*)?licen[cs]e", "Yes", "fill_combobox"),
    (r"access[\s_-]*to[\s_-]*(?:a[\s_-]*)?(?:car|vehicle|transport)", "Yes", "fill_combobox"),
    (r"own[\s_-]*(?:car|vehicle|transport|means[\s_-]*of[\s_-]*transport)", "Yes", "fill_combobox"),
    (r"willing.*(?:drive|travel[\s_-]*by[\s_-]*car)", "Yes", "fill_combobox"),

    # ── LANGUAGES (8 patterns) ──
    (r"language[\s_-]*(?:speak|spoken|skills?|proficiency|ability)", "English, Hindi", "fill"),
    (r"what[\s_-]*languages?[\s_-]*(?:do[\s_-]*you[\s_-]*)?speak", "English (Native), Hindi (Native)", "fill"),
    (r"proficien(?:t|cy)[\s_-]*(?:in[\s_-]*)?english|english[\s_-]*(?:proficiency|fluency|level)", "Native or bilingual", "fill_combobox"),
    (r"fluent[\s_-]*(?:in[\s_-]*)?english|english[\s_-]*fluency", "Native or bilingual", "fill_combobox"),
    (r"ielts|toefl|toeic|cambridge[\s_-]*english|english[\s_-]*(?:test|exam|certificate)", "Not applicable", "fill"),
    (r"proficien(?:t|cy)[\s_-]*(?:in[\s_-]*)?hindi|hindi[\s_-]*(?:proficiency|fluency)", "Native or bilingual", "fill_combobox"),
    (r"(?:first|native|primary|mother)[\s_-]*(?:language|tongue)", "English", "fill_combobox"),
    (r"(?:second|additional|other)[\s_-]*language", "Hindi", "fill_combobox"),

    # ── EMPLOYMENT TYPE (7 patterns) ──
    (r"full[\s_-]*time|part[\s_-]*time|employment[\s_-]*type", "Full-time", "fill_combobox"),
    (r"preferred[\s_-]*(?:employment|work|contract)[\s_-]*(?:type|arrangement)", "Full-time", "fill_combobox"),
    (r"looking[\s_-]*for[\s_-]*(?:permanent|full[\s_-]*time|contract)", "Full-time", "fill_combobox"),
    (r"permanent[\s_-]*(?:contract|position|role)|fixed[\s_-]*term", "Permanent", "fill_combobox"),
    (r"shift[\s_-]*(?:work|pattern)|work[\s_-]*(?:weekends?|evenings?|nights?)", "Yes", "fill_combobox"),
    (r"overtime|flexible[\s_-]*hours?|bank[\s_-]*holidays?", "Yes", "fill_combobox"),
    (r"on[\s_-]*call|standby|out[\s_-]*of[\s_-]*hours", "Yes", "fill_combobox"),

    # ── SOURCE / REFERRAL (4 patterns) ──
    (r"referr(?:al|ed)[\s_-]*(?:by|from|code|name|source)", "No", "fill"),
    (r"(?:were[\s_-]*you[\s_-]*)?referred[\s_-]*(?:by[\s_-]*)?(?:an[\s_-]*)?employee", "No", "fill"),
    (r"employee[\s_-]*(?:referral|refer)", "No", "fill"),
    (r"recruitment[\s_-]*(?:agency|consultant)|how.*apply", "Direct application", "fill"),

    # ── BACKGROUND CHECK / DBS / SECURITY (8 patterns) ──
    (r"background[\s_-]*check|pre[\s_-]*employment[\s_-]*(?:check|screen)", "Yes", "fill_combobox"),
    (r"dbs[\s_-]*check|disclosure[\s_-]*(?:and[\s_-]*)?barring", "Yes", "fill_combobox"),
    (r"willing[\s_-]*(?:to[\s_-]*)?undergo[\s_-]*(?:a[\s_-]*)?(?:background|security|dbs)[\s_-]*check", "Yes", "fill_combobox"),
    (r"security[\s_-]*clearance|(?:hold|possess)[\s_-]*(?:a[\s_-]*)?(?:security[\s_-]*)?clearance", "None", "fill_combobox"),
    (r"(?:sc|dv|ctc|bpss|esc)[\s_-]*clearance|level[\s_-]*of[\s_-]*clearance", "None", "fill_combobox"),
    (r"rehabilitation[\s_-]*of[\s_-]*offenders|roa|spent[\s_-]*conviction", "No convictions", "fill_combobox"),
    (r"non[\s_-]*compete|restrictive[\s_-]*covenant|conflict[\s_-]*of[\s_-]*interest", "No", "fill_combobox"),
    (r"gardening[\s_-]*leave|garden[\s_-]*leave", "No", "fill_combobox"),

    # ── CARING / ADJUSTMENTS (5 patterns) ──
    (r"caring[\s_-]*responsib|carer[\s_-]*(?:status|responsibilities)", "No", "fill"),
    (r"childcare|eldercare|dependan[tc]", "No", "fill"),
    (r"reasonable[\s_-]*adjust|workplace[\s_-]*adjust|access[\s_-]*(?:require|needs?)", "No", "fill"),
    (r"special[\s_-]*accommod|accommodat|support.*(?:during[\s_-]*)?(?:application|interview|assessment)", "No", "fill"),
    (r"assistive[\s_-]*tech|screen[\s_-]*reader|accessibility[\s_-]*(?:needs?|require)", "No", "fill"),

    # ── PREVIOUS EMPLOYMENT (2 patterns) ──
    (r"(?:current|former|ex)[\s_-]*employee|(?:do|have)[\s_-]*you[\s_-]*(?:currently[\s_-]*)?work[\s_-]*(?:for|at)", "No", "fill"),
    (r"(?:have[\s_-]*you[\s_-]*)?ever[\s_-]*(?:been|worked)[\s_-]*(?:employed[\s_-]*)?(?:by|with|at|for)", "No", "fill"),

    # ── TEAM MANAGEMENT (4 patterns) ──
    (r"(?:direct[\s_-]*)?report|(?:people|staff)[\s_-]*(?:managed|supervised)|team[\s_-]*size", "8", "fill"),
    (r"how[\s_-]*many[\s_-]*(?:people|staff|employees?)[\s_-]*(?:have[\s_-]*you[\s_-]*)?(?:managed|supervised|led)", "8", "fill"),
    (r"(?:managing|managed)[\s_-]*(?:a[\s_-]*)?team|line[\s_-]*management|leadership[\s_-]*experience|management[\s_-]*experience", "Yes", "fill_combobox"),
    (r"largest[\s_-]*team[\s_-]*(?:managed|led|supervised)", "8", "fill"),

    # ── TRAVEL / COMMUTE (4 patterns) ──
    (r"willing[\s_-]*(?:to[\s_-]*)?travel|comfortable[\s_-]*(?:with[\s_-]*)?travel", "Yes", "fill"),
    (r"(?:percentage|%|amount)[\s_-]*(?:of[\s_-]*)?travel", "Up to 25%", "fill"),
    (r"commut.*(?:to|distance|time)|travel[\s_-]*(?:to[\s_-]*)?(?:the[\s_-]*)?office", "Yes", "fill"),
    (r"travel[\s_-]*(?:domestic|international|abroad|overseas)", "Yes", "fill"),

    # ── REFERENCES (2 patterns) ──
    (r"(?:can[\s_-]*we[\s_-]*)?contact[\s_-]*(?:your[\s_-]*)?(?:current[\s_-]*)?(?:employer|referees?|references?)", "Yes", "fill"),
    (r"provide[\s_-]*refer|supply[\s_-]*refer|list[\s_-]*(?:your[\s_-]*)?refer", "Available upon request", "fill"),

    # ── CONSENT / GDPR / PRIVACY (8 patterns) — blocks submission if unchecked ──
    (r"(?:i[\s_-]*)?(?:agree|consent|accept).*(?:privacy|terms|data[\s_-]*(?:processing|protection|retention))", "true", "check"),
    (r"(?:privacy[\s_-]*)?(?:policy|notice)[\s_-]*(?:accept|agree|consent|checkbox)", "true", "check"),
    (r"retain.*(?:data|information|details).*(?:future|pool|consideration)", "Yes", "fill_combobox"),
    (r"marketing[\s_-]*(?:consent|communications?|emails?)", "No", "fill_combobox"),
    (r"third[\s_-]*part(?:y|ies).*(?:share|transfer|disclose)", "Yes", "fill_combobox"),
    (r"talent[\s_-]*(?:pool|community|network)[\s_-]*(?:consent|join|opt)", "Yes", "fill_combobox"),
    (r"information.*(?:accurate|true|correct|honest)", "true", "check"),
    (r"acknowledge[\s_-]*(?:and[\s_-]*)?(?:read|understood|reviewed)", "true", "check"),

    # ── SOCIAL MOBILITY / SOCIOECONOMIC (UK, 7 patterns) ──
    (r"(?:type|kind)[\s_-]*(?:of[\s_-]*)?school.*(?:attend|went)", "State-run or state-funded school", "fill_combobox"),
    (r"free[\s_-]*school[\s_-]*meals?\b", "No", "fill_combobox"),
    (r"parent(?:s'?|al)?[\s_-]*(?:occupation|employment|job)", "Professional", "fill_combobox"),
    (r"(?:main|highest)[\s_-]*(?:household[\s_-]*)?(?:earner|breadwinner)[\s_-]*(?:occupation|job)", "Professional", "fill_combobox"),
    (r"(?:parent|guardian).*(?:university|degree|higher[\s_-]*education)", "Yes", "fill_combobox"),
    (r"socio[\s_-]*economic[\s_-]*(?:background|status)", "Prefer not to say", "fill_combobox"),
    (r"first[\s_-]*(?:generation|in[\s_-]*family).*(?:university|higher[\s_-]*education|degree)", "No", "fill_combobox"),

    # ── GENDER REASSIGNMENT / TRANS STATUS (UK Equality Act) ──
    (r"gender[\s_-]*reassignment|transgender|trans[\s_-]*(?:gender|status)", "No", "fill_combobox"),

    # ── WORKDAY-SPECIFIC (10 patterns) ──
    (r"(?:have[\s_-]*you[\s_-]*)?previous(?:ly)?[\s_-]*(?:been[\s_-]*a[\s_-]*)?worker|previous[\s_-]*worker", "No", "fill_combobox"),
    (r"(?:name[\s_-]*)?suffix\b|^suffix$|name[\s_-]*suffix", "", "fill"),
    (r"^gpa$|grade[\s_-]*(?:point[\s_-]*)?average|cumulative[\s_-]*gpa|overall[\s_-]*(?:gpa|grade)", "3.8", "fill"),
    (r"hispanic[\s_-]*(?:or[\s_-]*)?latino|latino[\s_-]*(?:or[\s_-]*)?hispanic", "No", "fill_combobox"),
    (r"^source$|source[\s_-]*prompt|how.*(?:did[\s_-]*you[\s_-]*)?(?:hear|find|learn)[\s_-]*(?:about|of)", "Job Board", "fill_combobox"),
    (r"skills?[\s_-]*(?:prompt|entry|tag)", "Python, SQL, Machine Learning", "fill"),
    (r"first[\s_-]*year[\s_-]*attend|year[\s_-]*(?:started|began|enrolled)", "2025", "fill"),
    (r"last[\s_-]*year[\s_-]*attend|year[\s_-]*(?:finished|completed|graduated)", "2026", "fill"),
    (r"(?:create[\s_-]*)?account[\s_-]*(?:checkbox|agree|terms)", "true", "check"),
    (r"follow[\s_-]*company[\s_-]*(?:checkbox)?", "false", "check"),

    # ── GREENHOUSE / LEVER SPECIFIC (5 patterns) ──
    (r"(?:current[\s_-]*)?company[\s_-]*(?:name|employer)|^org$|current[\s_-]*organi[sz]ation", "Co-op", "fill"),
    (r"hispanic[\s_-]*ethnicity|hispanic[\s_-]*or[\s_-]*latino[\s_-]*ethnicity", "No", "fill_combobox"),
    (r"disability[\s_-]*status|self[\s_-]*identif.*disab", "No, I don't have a disability", "fill_combobox"),
    (r"(?:urls?\[)?\s*twitter\s*\]?", "", "fill"),
    (r"(?:urls?\[)?\s*(?:other|additional[\s_-]*(?:url|link))\s*\]?", PROFILE.get("portfolio", ""), "fill"),

    # ── DATE FIELDS (6 patterns) ──
    (r"date[\s_-]*of[\s_-]*birth|birth[\s_-]*date|dob|born[\s_-]*on", "1997-08-15", "fill_date"),
    (r"graduation[\s_-]*(?:date|year)|(?:when|year)[\s_-]*(?:did[\s_-]*you[\s_-]*)?graduat", "2026", "fill"),
    (r"(?:education|degree)[\s_-]*(?:start|begin)[\s_-]*(?:date|month|year)", "2025-01", "fill_date"),
    (r"(?:education|degree)[\s_-]*(?:end|finish|completion)[\s_-]*(?:date|month|year)", "2026-01", "fill_date"),
    (r"(?:employment|work|job)[\s_-]*(?:start)[\s_-]*(?:date|month|year)", "", "fill_date"),
    (r"(?:employment|work|job)[\s_-]*(?:end|finish|leave)[\s_-]*(?:date|month|year)", "", "fill_date"),

    # ── PHONE COUNTRY CODE / TYPE (2 patterns) ──
    (r"phone[\s_-]*(?:country[\s_-]*)?code|country[\s_-]*(?:calling[\s_-]*)?code|dialling[\s_-]*code", "United Kingdom (+44)", "fill_combobox"),
    (r"phone[\s_-]*(?:device[\s_-]*)?type|type[\s_-]*of[\s_-]*phone", "Mobile", "fill_combobox"),

    # ── YEARS OF EXPERIENCE (4 patterns) ──
    (r"total[\s_-]*(?:years?|yrs?)[\s_-]*(?:of[\s_-]*)?(?:professional|work)[\s_-]*experience", "3", "fill"),
    (r"how[\s_-]*many[\s_-]*(?:years?|yrs?)[\s_-]*(?:of[\s_-]*)?(?:experience|work)\b", "3", "fill"),
    (r"years?[\s_-]*(?:of[\s_-]*)?(?:relevant|related|industry)[\s_-]*experience", "2", "fill"),
    (r"(?:total|overall)[\s_-]*(?:work|professional)[\s_-]*experience[\s_-]*(?:in[\s_-]*)?years?", "3", "fill"),

    # ── CRIMINAL RECORD (UK granular, 3 patterns) ──
    (r"unspent[\s_-]*(?:criminal[\s_-]*)?(?:conviction|caution)", "No", "fill_combobox"),
    (r"(?:conviction|caution).*(?:not[\s_-]*)?(?:currently[\s_-]*)?filtered[\s_-]*(?:by[\s_-]*)?(?:the[\s_-]*)?dbs", "No", "fill_combobox"),
    (r"(?:children(?:'s)?|adults?)[\s_-]*barred[\s_-]*list", "No", "fill_combobox"),

    # ── CERTIFICATION / PROFESSIONAL BODY (4 patterns) ──
    (r"professional[\s_-]*(?:body|membership|registration|institute)", "No", "fill"),
    (r"(?:chartered|registered)[\s_-]*(?:status|professional|member)", "No", "fill"),
    (r"(?:industry|professional)[\s_-]*(?:certification|accreditation)[\s_-]*(?:name|type|held)", "None", "fill"),
    (r"(?:cpd|continuing[\s_-]*professional[\s_-]*development)", "N/A", "fill"),

    # ── NI NUMBER / TAX (UK, 2 patterns) ──
    (r"national[\s_-]*insurance[\s_-]*(?:number|no\.?\b)|^ni[\s_-]*(?:number|no\.?\b)$|^nino$", "Will provide on start", "fill"),
    (r"tax[\s_-]*(?:code|reference)|p45|starter[\s_-]*checklist", "Will provide on start", "fill"),

    # ── EMERGENCY CONTACT (4 patterns) ──
    (r"emergency[\s_-]*contact[\s_-]*(?:name|person)", "Available on request", "fill"),
    (r"emergency[\s_-]*contact[\s_-]*(?:phone|number|tel)", "Available on request", "fill"),
    (r"emergency[\s_-]*contact[\s_-]*(?:relation|relationship)", "Available on request", "fill"),
    (r"next[\s_-]*of[\s_-]*kin", "Available on request", "fill"),

    # ── DRUG TEST / HEALTH (5 patterns) ──
    (r"drug[\s_-]*(?:test|screen)|substance[\s_-]*(?:test|screen)", "Yes", "fill_combobox"),
    (r"health[\s_-]*(?:condition|issue|problem).*(?:affect|impact|prevent)", "No", "fill_combobox"),
    (r"(?:physical|medical)[\s_-]*(?:fitness|exam|examination)", "Yes", "fill_combobox"),
    (r"night[\s_-]*work[\s_-]*(?:capability|suitable|assessment)", "Yes", "fill_combobox"),
    (r"registered[\s_-]*(?:with[\s_-]*)?(?:health[\s_-]*(?:and[\s_-]*)?care[\s_-]*professions?|hcpc)", "No", "fill_combobox"),

    # ── PROFICIENCY / SKILL RATINGS (3 patterns) ──
    (r"(?:what[\s_-]*is[\s_-]*)?(?:your[\s_-]*)?(?:level|proficiency)[\s_-]*(?:of|in|with)[\s_-]*(?:experience|expertise)", "Intermediate", "fill_combobox"),
    (r"(?:expert|advanced|intermediate|beginner)[\s_-]*(?:level)?\b.*(?:select|choose)", "Intermediate", "fill_combobox"),
    (r"(?:on[\s_-]*a[\s_-]*)?scale[\s_-]*(?:of[\s_-]*)?(?:1[\s_-]*(?:to|-)[\s_-]*(?:5|10))", "4", "fill"),

    # ── COMPENSATION (international, 4 patterns) ──
    (r"(?:expected|current)[\s_-]*ctc|cost[\s_-]*to[\s_-]*company", "32000", "fill"),
    (r"gross[\s_-]*(?:monthly|annual)[\s_-]*(?:salary|pay|compensation)", "32000", "fill"),
    (r"compensation[\s_-]*(?:type|structure|package)", "Salary", "fill_combobox"),
    (r"bonus[\s_-]*(?:expect|require|structure)|equity[\s_-]*(?:expect|require|stock|shares)", "N/A", "fill"),

    # ── HOURS / SCHEDULE (3 patterns) ──
    (r"(?:how[\s_-]*many[\s_-]*)?hours?[\s_-]*(?:available|per[\s_-]*week|weekly)", "40", "fill"),
    (r"(?:minimum|maximum)[\s_-]*hours?[\s_-]*(?:per[\s_-]*week|weekly)", "40", "fill"),
    (r"preferred[\s_-]*(?:working[\s_-]*)?hours?|work[\s_-]*schedule[\s_-]*prefer", "Full-time (40 hours)", "fill"),

    # ── BANK DETAILS (UK onboarding, 3 patterns) ──
    (r"(?:bank[\s_-]*)?sort[\s_-]*code", "Will provide on start", "fill"),
    (r"(?:bank[\s_-]*)?account[\s_-]*(?:number|no\.?\b)", "Will provide on start", "fill"),
    (r"(?:bank[\s_-]*)?(?:account[\s_-]*)?name\b.*(?:bank|account)", "Will provide on start", "fill"),
]


def _clean_label(field: FieldInfo) -> str:
    """Get the best label for a field, cleaning up placeholder noise."""
    label = field.label or ""
    if label.strip().lower() in _PLACEHOLDER_VALUES:
        label = field.group_label or field.fieldset_legend or ""
    # Also check dom_context for better label
    if not label and field.dom_context:
        label = field.dom_context.split("|")[0].strip()
    return label


def deterministic_fill(
    snapshot: PageSnapshot,
    *,
    job_context: dict[str, Any] | None = None,
    platform: str = "unknown",
) -> list[Action]:
    """Phase 1: Match fields by label patterns. Instant, free, high accuracy for known fields."""
    actions: list[Action] = []
    matched_selectors: set[str] = set()

    for field in snapshot.fields:
        if field.input_type == "file":
            continue  # State machine handles uploads
        if field.current_value and field.current_value.strip().lower() not in _PLACEHOLDER_VALUES:
            continue  # Already filled

        label = _clean_label(field)
        label_lower = label.lower().strip()
        if not label_lower:
            continue

        # Special case: "Job Advert Reference" — use platform
        if re.search(r"job\s*advert|how.*(?:hear|find)|referral\s*source", label_lower):
            value = (platform or "unknown").title()
            if job_context and job_context.get("platform"):
                value = job_context["platform"].title()
            actions.append(Action(type="fill", selector=field.selector, value=value))
            matched_selectors.add(field.selector)
            logger.info("  DET → %s = %s (job advert ref)", field.selector[:40], value)
            continue

        # Special case: phone number field with empty label next to country code combobox
        if not label_lower and field.input_type == "text":
            dom = (field.dom_context or "").lower()
            if "mobile" in dom or "phone" in dom or "+44" in dom:
                actions.append(Action(type="fill", selector=field.selector, value=PROFILE["phone"]))
                matched_selectors.add(field.selector)
                logger.info("  DET → %s = %s (phone from context)", field.selector[:40], PROFILE["phone"])
                continue

        # Try deterministic rules
        for pattern, value, atype in _DETERMINISTIC_RULES:
            if re.search(pattern, label_lower, re.IGNORECASE):
                if not value:
                    break  # Skip empty values
                # Remap action type based on actual field type
                ftype = field.input_type
                role = field.attributes.get("role", "")
                is_combobox = role == "combobox" or ftype in ("search_autocomplete", "combobox", "custom_select")
                if is_combobox and atype == "fill":
                    atype = "fill_combobox"
                elif not is_combobox and atype == "fill_combobox":
                    atype = "fill"  # Text field, not a combobox
                if ftype == "rich_text" and atype == "fill":
                    atype = "fill_contenteditable"

                actions.append(Action(type=atype, selector=field.selector, value=value))
                matched_selectors.add(field.selector)
                logger.info("  DET → %s [%s] = %s", field.selector[:40], atype, value[:60])
                break

    logger.info("Phase 1 (deterministic): %d/%d fields matched", len(actions), len(snapshot.fields))
    return actions


# ---------------------------------------------------------------------------
# Phase 2: LLM for remaining fields (with real dropdown options)
# ---------------------------------------------------------------------------

def _build_fields_description(fields: list[FieldInfo]) -> str:
    """Build field descriptions for the LLM — only unmatched fields."""
    parts: list[str] = []
    for i, f in enumerate(fields):
        label = _clean_label(f)
        desc = f"Field {i + 1}:"
        desc += f"\n  selector: {f.selector}"
        desc += f"\n  type: {f.input_type}"
        desc += f"\n  label: {label!r}"
        if f.required:
            desc += "\n  required: YES"
        if f.current_value and f.current_value.strip().lower() not in _PLACEHOLDER_VALUES:
            desc += f"\n  current_value: {f.current_value!r} (ALREADY FILLED)"
        if f.options:
            desc += f"\n  options: {f.options}"
        dom_ctx = getattr(f, "dom_context", "") or ""
        if dom_ctx:
            desc += f"\n  dom_context: {dom_ctx!r}"
        if f.help_text:
            desc += f"\n  help_text: {f.help_text!r}"
        if f.group_label:
            desc += f"\n  group_label: {f.group_label!r}"
        if f.fieldset_legend:
            desc += f"\n  fieldset_legend: {f.fieldset_legend!r}"
        if f.error_text:
            desc += f"\n  error: {f.error_text!r}"
        label_sources = getattr(f, "label_sources", None)
        if label_sources:
            desc += f"\n  label_sources: {label_sources}"
        parts.append(desc)
    return "\n\n".join(parts) if parts else "(no fields)"


def analyze_remaining_fields(
    snapshot: PageSnapshot,
    remaining_fields: list[FieldInfo],
    *,
    job_context: dict[str, Any] | None = None,
    platform: str = "unknown",
) -> list[Action]:
    """Phase 2: LLM call for fields not matched by deterministic rules.

    Called AFTER click-to-reveal enriches fields with real dropdown options.
    """
    if not remaining_fields:
        return []

    fields_desc = _build_fields_description(remaining_fields)
    logger.info("Phase 2 (LLM): analyzing %d remaining fields", len(remaining_fields))

    job_info = ""
    if job_context:
        job_info = (
            f"\n## Job Context\n"
            f"- Title: {job_context.get('job_title', 'unknown')}\n"
            f"- Company: {job_context.get('company', 'unknown')}\n"
            f"- Location: {job_context.get('location', 'unknown')}\n"
            f"- Platform: {job_context.get('platform', 'unknown')}\n"
        )

    page_context = ""
    if snapshot.page_text_preview:
        page_context = f"\n## Page Context\n{snapshot.page_text_preview[:800]}\n"

    prompt = f"""You are a job application agent. Fill the remaining form fields below.

## Rules
- For dropdowns with options listed, pick the EXACT option text
- For text fields, fill with the appropriate profile data
- For date fields, use YYYY-MM-DD
- For checkboxes, answer "true" or "false"
- If a field asks about a skill not in my profile, default to "2" years
- For open-ended questions, write 2-3 professional sentences
- Skip fields you cannot confidently fill

{_PROFILE_CONTEXT}
{job_info}
{page_context}

## Fields to Fill
{fields_desc}

Respond with ONLY a JSON array:
{{"selector": "<exact CSS selector>", "value": "<answer>", "type": "<action_type>"}}

Action types: fill, select, fill_radio_group, check, fill_date, fill_combobox, fill_contenteditable, fill_tag_input

Return [] if no fields should be filled."""

    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-5-mini",
            max_tokens=2000,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        logger.info("Phase 2 LLM response (%d chars)", len(raw))

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        fill_plan: list[dict[str, str]] = json.loads(raw)

        actions: list[Action] = []
        for entry in fill_plan:
            selector = entry.get("selector", "")
            value = entry.get("value", "")
            action_type = entry.get("type", "fill")

            if not selector or not value:
                continue

            # Skip file inputs
            field_match = next(
                (f for f in remaining_fields if f.selector == selector or selector in f.selector or f.selector in selector),
                None,
            )
            if field_match and field_match.input_type == "file":
                continue

            # Validate + remap action type
            valid_types = {"fill", "select", "fill_radio_group", "check", "fill_date",
                           "fill_combobox", "fill_contenteditable", "fill_tag_input"}
            if action_type not in valid_types:
                action_type = "fill"

            if field_match:
                ftype = field_match.input_type
                role = field_match.attributes.get("role", "")
                is_combobox = role == "combobox" or ftype in ("search_autocomplete", "combobox", "custom_select")
                if is_combobox and action_type in ("fill", "select", "fill_autocomplete", "fill_custom_select"):
                    action_type = "fill_combobox"
                elif ftype == "rich_text" and action_type == "fill":
                    action_type = "fill_contenteditable"

            actions.append(Action(type=action_type, selector=selector, value=value))
            logger.info("  LLM → %s [%s] = %s", selector[:40], action_type, str(value)[:60])

        return actions

    except json.JSONDecodeError as exc:
        logger.error("Phase 2: failed to parse LLM JSON: %s", exc)
        return []
    except Exception as exc:
        logger.error("Phase 2: LLM call failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Legacy entry point (for backwards compat with state machine)
# ---------------------------------------------------------------------------

def analyze_form_page(
    snapshot: PageSnapshot,
    *,
    job_context: dict[str, Any] | None = None,
    platform: str = "unknown",
) -> list[Action]:
    """Full analysis: deterministic fill + LLM for remaining.

    NOTE: This does NOT do click-to-reveal (needs bridge access).
    The orchestrator calls deterministic_fill + reveal + analyze_remaining_fields
    directly for the full two-phase flow. This is the fallback single-call path.
    """
    # Strip placeholders
    for f in snapshot.fields:
        if f.current_value and f.current_value.strip().lower() in _PLACEHOLDER_VALUES:
            f.current_value = ""

    # Phase 1: deterministic
    det_actions = deterministic_fill(snapshot, job_context=job_context, platform=platform)
    det_selectors = {a.selector for a in det_actions}

    # Phase 2: LLM for unmatched non-file fields
    remaining = [
        f for f in snapshot.fields
        if f.selector not in det_selectors
        and f.input_type != "file"
        and (not f.current_value or f.current_value.strip().lower() in _PLACEHOLDER_VALUES)
    ]
    llm_actions = analyze_remaining_fields(
        snapshot, remaining, job_context=job_context, platform=platform,
    )

    all_actions = det_actions + llm_actions
    # Sort in DOM order
    field_order = {f.selector: idx for idx, f in enumerate(snapshot.fields)}
    all_actions.sort(key=lambda a: field_order.get(a.selector, 9999))

    logger.info("FormAnalyzer total: %d actions (%d det + %d llm)",
                len(all_actions), len(det_actions), len(llm_actions))
    return all_actions
