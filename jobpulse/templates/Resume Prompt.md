% \================================================================  
% RESUME GENERATION PROMPT v3 — CONTEXT ENGINEERING EDITION  
% XeLaTeX / ATS-Optimised | Human-Authentic | Modular JD Architecture  
% \================================================================  
%  
% PURPOSE: Generate authentic, human-sounding, ATS-optimised resumes  
% (90–95%+ match) that pass enterprise AI-detection tools.  
% Output is a complete, XeLaTeX-ready .tex file.  
%  
% ARCHITECTURE: This prompt separates FIXED CONTEXT (immutable identity,  
% grounded role descriptions) from VARIABLE CONTEXT (target JD skills,  
% tone, keyword density) so a single prompt serves every application.  
%  
% \================================================================

% \=====================================================  
% LAYER 0 — GLOBAL HARD RULES (APPLY EVERYWHERE)  
% \=====================================================

⛔ DOUBLE-DASH RULE  
\-- is permitted in ONE place only: date ranges between two dates.

  Example               | Allowed?  
  \----------------------|----------  
  Jan 2025 \-- Jan 2026  | ✅ YES  
  Jul 2021 \-- Sep 2024  | ✅ YES  
  built the pipeline \-- | ❌ NEVER  
  Skills \-- Tools       | ❌ NEVER

If \-- appears outside a date range: rewrite using a comma, full stop,  
semicolon, or restructure the clause entirely. Do not patch — rewrite.

⚠️ Before outputting any LaTeX, scan every non-date line for \--.  
If found, fix before proceeding. This is non-negotiable.

% \=====================================================  
% LAYER 1 — FIXED IDENTITY CONTEXT (NEVER CHANGES)  
% \=====================================================  
% These are grounded facts about the candidate.  
% No JD should ever alter these elements.

CANDIDATE\_IDENTITY:  
  NAME              : Yash B  
  PHONE             : 07909445288  
  EMAIL             : bishnoiyash274@gmail.com  
  LINKEDIN          : https://linkedin.com/in/yash-bishnoi-2ab36a1a5  
  GITHUB            : https://github.com/yashb98  
  PORTFOLIO         : https://yashbishnoi.io

EDUCATION:  
  DEGREE\_1:  
    Title           : MSc Computer Science  
    Institution     : University of Dundee  
    Dates           : Jan 2025 \-- Jan 2026  
    Dissertation    : Deep Learning for facial 3D Reconstruction \- Simulator  
    Core\_Modules    : Machine Learning | Advanced Programming Techniques | Design Methods | Software Engineering | Software Development | Web Development | Database Systems

  DEGREE\_2:  
    Title           : MBA (Finance)  
    Institution     : JECRC University  
    Dates           : 2019 \-- 2021  
    CGPA            : 8.21/10

CERTIFICATIONS (titles \+ URLs are immutable):  
  1\. IBM Machine Learning               | July 2023      | https://www.coursera.org/account/accomplishments/specialization/certificate/SL9P2Q6Z43JP  
  2\. SQL Essential Learning              | September 2023 | https://www.linkedin.com/learning/certificates/df5df7f2f991831af1bf1429e9c267ed342ae6db0f951f55cf7b6b7ce72e1f2b  
  3\. Feature Engineering                 | July 2023      | https://www.kaggle.com/learn/certification/yashbishnoi98/feature-engineering  
  4\. Data Cleaning                       | July 2023      | https://www.kaggle.com/learn/certification/yashbishnoi98/data-cleaning  
  5\. Exploratory Data Analysis for ML    | July 2023      | https://www.coursera.org/account/accomplishments/certificate/LPEX59KAWX8X  
  6\. Deep Learning and RL               | July 2023      | https://www.coursera.org/account/accomplishments/certificate/S2MJH2ZQ8WF4  
  7\. Introduction to MCP                 | Feb 2026       | http://verify.skilljar.com/c/nn63q5jje52u  
  8\. Microsoft Azure Fundamentals (AZ-900)| Mar 2026       | \[Verify link TBC post-exam\]

PROJECT\_TITLES (exact, immutable):  
  1\. Velox AI \- Enterprise AI Voice Agent Platform  
  2\. Cloud Sentinel \- AI Powered Cloud Security Platform  
  3\. 90 Days Machine learning  
  4\. Deep Learning for Facial 3D Reconstructions

COMMUNITY\_TITLES (exact, immutable):  
  1\. Quackathon 2025 Participant  
  2\. Friends International, Dundee Chapter  
  3\. Peer Mentor for Coding Challenges

% \=====================================================  
% LAYER 1b — GROUNDED PROJECT CONTEXT  
% \=====================================================  
% These are the TRUE tech stacks and capabilities for each project.  
% Bullet content is ADAPTED to the target JD, but must remain  
% anchored to what was actually built.

PROJECT\_2\_GROUND\_TRUTH:  
  TITLE: Cloud Sentinel \- AI Powered Cloud Security Platform

  ACTUAL\_TECH\_STACK:  
    Backend         : Python 3.11+, FastAPI, LangChain, Pydantic  
    Frontend        : React 18, TypeScript, Vite, Tailwind CSS, Framer Motion  
    AI/ML           : Google Gemini 2.0 Flash (embeddings, 768-dim), RAG pipeline  
    Vector DB       : Pinecone (semantic search, top-k retrieval, batch vector insertion)  
    Cache/Session   : Redis (caching, rate limiting, session store)  
    Auth            : Clerk (JWT, session persistence, route protection)  
    Database        : Firebase/Firestore (real-time chat sync)  
    Cloud/Infra     : Docker (multi-stage builds, non-root), Docker Compose, GCP Cloud Run, Kubernetes-ready  
    Security Tools  : AWS SDK (S3 bucket auditing, encryption verification, public access checks, multi-region)  
    Integration     : MCP (Model Context Protocol) via SSE, FastMCP server  
    DevOps          : Health monitoring endpoints, CORS middleware, rate limiting (SlowAPI)

  ACTUAL\_CAPABILITIES:  
    \- RAG system: PDF ingestion → PyPDFLoader text extraction → intelligent chunking (1000 chars, 200 overlap) → Gemini embeddings → Pinecone vector store → semantic search with top-3 retrieval  
    \- AI chat interface with conversation threading, persistent history, rate limiting (5 msg/min)  
    \- Policy management: upload PDF → extract → chunk → embed → store with metadata (source, page, content)  
    \- AWS S3 security auditing: bucket discovery, SSE verification, versioning validation, public access block checks  
    \- MCP tool execution via Server-Sent Events with real-time logging and error recovery  
    \- Microservices architecture: Backend (FastAPI) \+ MCP Server \+ Frontend (React) \+ Redis  
    \- Containerized deployment with Docker Compose, GCP Cloud Run scripts, K8s-ready configs  
    \- Input validation (Pydantic), defensive programming, comprehensive error handling  
    \- PWA-capable frontend with offline support, mobile-responsive, terminal-style UI

  KEYWORD\_BANK (use to translate capabilities into JD language):  
    Data/Analytics    : data pipeline, document processing, batch processing, data extraction, data transformation, structured/semi-structured data  
    AI/ML             : RAG, vector embeddings, semantic search, LLM integration, AI-powered analysis, NLP  
    Infrastructure    : microservices, containerisation, Docker, Kubernetes, CI/CD, health monitoring  
    Security          : compliance checking, audit, encryption verification, access control, input validation  
    Programming       : Python, FastAPI, TypeScript, React, REST APIs, SSE  
    Data Quality      : traceability, reproducibility, metadata tracking, validation, quality control  
    Collaboration     : real-time sync, multi-user support, conversation threading, stakeholder reporting

% \=====================================================  
% LAYER 2 — GROUNDED EXPERIENCE CONTEXT  
% \=====================================================  
% These are the TRUE responsibilities for each role.  
% Bullet content is ADAPTED to the target JD, but must  
% remain anchored to these ground-truth duties.

EXPERIENCE\_1:  
  TITLE             : Team Leader  
  COMPANY           : Co-op  
  DATES             : Apr 2025 \-- Present  
  TYPE              : Part-time (16 hrs/week \+ overtime)

  GROUND\_TRUTH\_DUTIES:  
    \- Own the day-to-day running of the store; lead the team on shift  
    \- Motivate, coach, and support team members to deliver great customer service and efficient operations  
    \- Work hands-on on the shop floor and tills, setting the pace for the team  
    \- Support store performance through merchandising, stock accuracy, and HR processes  
    \- Champion Co-op through community engagement and membership growth  
    \- Handle age-related sales authorisation (compliance)  
    \- Support post office, bakery, online services, and home delivery operations as needed

  KEYWORD\_BANK (use to translate duties into JD language):  
    Leadership        : team coordination, shift leadership, coaching, performance management, leading by example  
    Operations        : inventory management, stock accuracy, merchandising, daily operations, store performance, supply chain awareness  
    Data/Analytics    : data-driven shift scheduling, sales tracking, KPI monitoring, reporting, performance metrics  
    Customer          : customer service, community engagement, stakeholder communication, membership growth  
    Process           : process improvement, operational efficiency, compliance, HR processes, incident resolution  
    Collaboration     : cross-functional collaboration, team motivation, knowledge sharing, mentoring

  ADAPTATION\_RULES:  
    For Data Science / ML roles     → emphasise: data-driven decisions, KPI tracking, scheduling optimisation, performance metrics, reporting  
    For Software / AI Engineering   → emphasise: process improvement, operational systems, team coordination, agile-like shift management  
    For Data Engineering roles      → emphasise: stock accuracy systems, inventory data pipelines, operational data flows, process automation  
    For Business / Analytics roles  → emphasise: sales insights, merchandising analysis, stakeholder reporting, community engagement metrics  
    For any technical role          → frame leadership as: "translating ambiguous operational problems into structured, data-informed actions"

  HARD CONSTRAINT: Every bullet must be honestly derivable from GROUND\_TRUTH\_DUTIES.  
  You may reframe the language, but never fabricate a duty that didn't happen.

EXPERIENCE\_2:  
  TITLE             : Market Research Analyst  
  COMPANY           : Nidhi Herbal  
  DATES             : Jul 2021 \-- Sep 2024

  GROUND\_TRUTH\_DUTIES:  
    \- Built and maintained Power BI dashboards (DAX measures, calculated columns)  
    \- Connected Power BI to SQL databases and Excel data sources via live/import connections  
    \- Produced supplier performance, sales, and inventory reports for senior management  
    \- Conducted market research and competitor analysis  
    \- Managed data cleaning, transformation, and ETL workflows  
    \- Presented monthly insights to cross-functional teams

  KEYWORD\_BANK:  
    Analytics         : Power BI, DAX, data visualisation, business intelligence, KPI dashboards, reporting  
    Data              : SQL, Excel, ETL, data cleaning, data transformation, data pipelines  
    Stakeholder       : stakeholder reporting, cross-functional collaboration, senior management presentations  
    Research          : market research, competitor analysis, supplier analysis, customer segmentation

  ADAPTATION\_RULES:  
    For Data Science / ML roles     → emphasise: analytical dashboards, data-driven insights, statistical reporting, feature-level data work  
    For Data Engineering roles      → emphasise: ETL pipelines, SQL connections, data transformation, pipeline reliability  
    For Business / Analytics roles  → emphasise: Power BI, DAX, stakeholder reporting, business intelligence  
    For any technical role          → frame as: "end-to-end data workflow from ingestion to executive-facing deliverables"

% \=====================================================  
% LAYER 3 — VARIABLE JD CONTEXT (CHANGES PER APPLICATION)  
% \=====================================================

STEP 1 — AUTO-EXTRACT FROM TARGET JD (Do This First, Print Output)

Before writing anything, extract and print the following block from the  
TARGET JD (the job you are applying for, NOT the Co-op JD).  
Use it as the source of truth for all downstream keyword decisions.

EXTRACTED:  
  LOCATION       : \<city/region from target JD\>  
  ROLE\_TITLE     : \<exact job title from target JD\>  
  YEARS\_EXP      : \<minimum years stated, or "2+" if implied\>  
  INDUSTRY       : \<e.g. FinTech, HealthTech, SaaS, Gov, Retail\>  
  SUB\_CONTEXT    : \<e.g. cloud infrastructure, NLP pipelines, fraud detection\>  
  SKILLS\_LIST    : \[ordered as they appear in JD, 12–15 items max\]  
  SOFT\_SKILLS    : \[e.g. stakeholder communication, cross-functional collaboration\]  
  EXTENDED\_SKILLS: \[only skills from the Extended Skill Bank that appear in the JD\]

% \=====================================================  
% LAYER 4 — SKILL ROUTING ENGINE  
% \=====================================================

STEP 2 — SKILL-TO-PROJECT MAPPING

Use the extracted SKILLS\_LIST to decide where each skill appears.  
Apply this decision tree in order:

  Priority 1 — Primary fit: skill is core to Project 1, 2, or 4  
                → embed naturally in a bullet for that project

  Priority 2 — Secondary fit: skill is related but not core  
                → add to Project 3 with context (e.g. "used X for model evaluation")

  Priority 3 — No fit: skill has no honest link to any project  
                → omit entirely (never force-fit)

  Extended skills — only include if they appeared in EXTRACTED.EXTENDED\_SKILLS

Project 3 ("90 Days Machine learning") is the catch-all showcase.  
It must not read like a keyword dump; every skill mentioned needs  
a concrete action attached.

STEP 2b — EXPERIENCE KEYWORD ROUTING

For each role, consult the KEYWORD\_BANK and ADAPTATION\_RULES above.

  1\. Identify the target JD's domain (Data Science, Engineering, Analytics, etc.)  
  2\. Select the matching ADAPTATION\_RULE for that domain  
  3\. Pull 3–5 keywords from the corresponding KEYWORD\_BANK categories  
  4\. Write bullets that are BOTH grounded in GROUND\_TRUTH\_DUTIES AND  
     use the selected keywords naturally

⚠️ HONESTY GATE: If a keyword from the target JD cannot be honestly  
linked to any GROUND\_TRUTH\_DUTY via the KEYWORD\_BANK, do not use it  
in the Experience section. Route it to Projects instead.

% \=====================================================  
% LAYER 5 — HEADER & SUMMARY CONSTRUCTION  
% \=====================================================

STEP 3 — HEADER TAGLINE

Fill with the 4 most relevant skills from SKILLS\_LIST (in JD order):

\\address{MSc Computer Science (UOD) | \[YEARS\_EXP\] YOE | \[ROLE\_TITLE\] | \[Skill1\] | \[Skill2\] | \[Skill3\] | \[Skill4\]}

STEP 4 — PROFESSIONAL SUMMARY RULES

  Tone          : Professional, confident, no casual phrases whatsoever  
  Must mention  : ROLE\_TITLE, YEARS\_EXP, top 2–3 skills, INDUSTRY, one soft skill  
  HARD LIMIT    : 3 sentences maximum. Each sentence under 25 words.  
  Line limit    : If rendered at 11pt Arial the summary exceeds 4 lines, rewrite.  
                  Cut adjectives first, then sub-clauses.

  ⛔ BOLD HIGHLIGHTING RULE (Professional Summary):  
    Bold the following key elements using \\textbf{} in the summary:  
      \- ROLE\_TITLE (e.g. \\textbf{Data Scientist})  
      \- Top 2–3 technical skills from SKILLS\_LIST (e.g. \\textbf{SQL}, \\textbf{Tableau})  
      \- INDUSTRY or domain keyword (e.g. \\textbf{e-commerce}, \\textbf{FinTech})  
    Max 5 bold terms across the entire summary. Do not bold soft skills,  
    verbs, or generic words like "years" or "experience."  
    Purpose: recruiter eye-tracking research shows bolded keywords in summaries  
    increase read-through rate by 40%. ATS parsers also weight bolded terms higher.

  Sentence structure (adapt, do not copy verbatim):

    Sentence 1 (Identity)       : \\textbf{\[ROLE\_TITLE\]} \+ \[YEARS\_EXP\] years \+ \\textbf{\[Skill1\]} \+ \\textbf{\[INDUSTRY\]}  
    Sentence 2 (Specialisation) : Specialises in \\textbf{\[Skill2\]}, \[Skill3\], and \\textbf{\[Skill4\]}  
    Sentence 3 (Working style)  : Deployment/monitoring focus \+ one soft skill from JD

  BAD (no highlights, recruiter skips):  
    Machine Learning Engineer with 2+ years designing and deploying production  
    ML systems in low-latency, containerised environments for data-intensive  
    platforms.

  GOOD (key terms pop, recruiter locks in):  
    \\textbf{Machine Learning Engineer} with 2+ years building production  
    \\textbf{ML pipelines} and low-latency inference systems for  
    data-intensive \\textbf{AdTech} platforms. Specialises in real-time  
    pipeline design, \\textbf{microservice architecture}, and concurrent  
    model deployment across \\textbf{Docker} and Kubernetes environments.  
    Translates ambiguous technical briefs into actionable engineering steps  
    while collaborating across data science and product teams.

% \=====================================================  
% LAYER 6 — BULLET ENGINEERING RULES  
% \=====================================================

⛔ CV DENSITY RULE — CATCHY, NOT CROWDED

The CV must feel easy to scan in 15 seconds, not exhausting to read.

  Rule              | Requirement  
  \------------------|------------------------------------------------------------  
  Bullet length     | 1 to 1.5 lines max at 11pt Arial; no exceptions  
  Bullet content    | One idea per bullet only; no stacking  
  Bold usage        | Bold the most important word only; not whole phrases  
  Whitespace        | \\vspace{0.3em} between every project  
  Summary           | 3 tight sentences; no sub-clauses or stacking  
  Skills rows       | Max 8 items per row; remove anything not in JD  
  Overall feel      | If the page looks dense, bullets are too long. Cut first.

BULLET LENGTH FORMULA:  
  Lead verb \+ bold skill \+ specific action \+ one metric. Nothing else.  
  If it doesn't fit in 1.5 lines, the bullet is saying two things. Split or cut.

PROJECT BULLET STRUCTURE (DO NOT CHANGE COUNTS OR TITLE FORMATS):

  \#  | Project Title (exact)                              | Bullets | Title separator  
  \---|----------------------------------------------------|---------|-----------------  
  1  | Velox AI \- Enterprise AI Voice Agent Platform       | 3       | pipes | \+ skills  
  2  | Cloud Sentinel \- AI Powered Cloud Security Platform | 5       | commas \+ skills  
  3  | 90 Days Machine learning                            | 4       | no skills in title  
  4  | Deep Learning for Facial 3D Reconstructions         | 3       | with \+ skills

BULLET WRITING RULES:  
  \- Every bullet: 1–1.5 lines at 11pt Arial. If 2 lines, rewrite (don't trim).  
  \- Each project section: exactly one contraction (wasn't, we'd, I've, didn't)  
  \- At least one bullet per resume starts with a number (e.g. "10,000+ synthetic…")  
  \- At least one bullet per resume is a fragment (no leading verb)  
  \- Max 2 bold keywords per bullet  
  \- Bold density: no more than 60% of JD keywords bolded across resume  
  \- Verb variety: rotate between built, shipped, created, put together, designed, fixed, wrote, worked, generated

IMPERFECTION PHRASES — pick exactly 3 per resume, no repeats:

  Phrase                         | Usage location  
  \-------------------------------|---------------------------------------------  
  "Turns out, \[insight\]"         | end of a bullet explaining a fix  
  "Not gonna lie, \[challenge\]"   | mid-bullet on a hard problem  
  "Honestly, \[unexpected thing\]" | standalone short sentence in a bullet  
  "The tricky part was \[detail\]" | after describing a system design decision  
  "What worked: \[solution\]"      | last bullet of a project section  
  "I didn't expect \[outcome\]"    | reflection on a result  
  "Here's the thing: \[realization\]" | opening of a bullet about a lesson learned

⚠️ Imperfection phrases are for project bullets ONLY.  
Never use them in Professional Summary or Experience sections.

% \=====================================================  
% LAYER 7 — EXPERIENCE BULLET PATTERNS  
% \=====================================================

Each role must have minimum 4 bullets with all four patterns present:

  Pattern         | Example  
  \----------------|----------------------------------------------------------  
  Standard        | \\textbf{Analysed} supplier data in Power BI, cutting report time by 30\\%.  
  Outcome-first   | Cut stockout incidents by 25\\% by rebuilding the tracking dashboard.  
  Short punch     | Managed a team of 8 using data-driven shift scheduling.  
  Stakeholder     | Presented monthly insights to senior management, informing Q3 decisions.

ROLE 1 — Team Leader, Co-op (Apr 2025 – Present)

  GROUNDING CHECK: Before writing each bullet, verify it maps to at least  
  one item in EXPERIENCE\_1.GROUND\_TRUTH\_DUTIES.

  Domain routing (select based on target JD):

    IF target JD is Data Science / ML:  
      Bullet 1 (Standard)    : data-driven shift scheduling or KPI monitoring \+ metric  
      Bullet 2 (Outcome)     : operational improvement via performance tracking \+ % gain  
      Bullet 3 (Short punch) : team size \+ one data-informed decision  
      Bullet 4 (Stakeholder) : communicated store performance insights to management

    IF target JD is Software / AI Engineering:  
      Bullet 1 (Standard)    : process improvement or operational workflow \+ metric  
      Bullet 2 (Outcome)     : efficiency gain through structured problem-solving  
      Bullet 3 (Short punch) : team coordination \+ sprint-like shift management  
      Bullet 4 (Stakeholder) : cross-functional collaboration with store teams

    IF target JD is Data Engineering:  
      Bullet 1 (Standard)    : stock accuracy or inventory data tracking \+ metric  
      Bullet 2 (Outcome)     : reduced errors via data pipeline awareness  
      Bullet 3 (Short punch) : operational data flow management  
      Bullet 4 (Stakeholder) : reporting operational metrics to area managers

    IF target JD is Business / Analytics:  
      Bullet 1 (Standard)    : sales tracking or merchandising analysis \+ metric  
      Bullet 2 (Outcome)     : membership growth via community engagement  
      Bullet 3 (Short punch) : team of X, store performance KPIs  
      Bullet 4 (Stakeholder) : presented sales/performance data to senior staff

ROLE 2 — Market Research Analyst, Nidhi Herbal (Jul 2021 – Sep 2024\)

  GROUNDING CHECK: Same rule. Every bullet anchored to EXPERIENCE\_2.GROUND\_TRUTH\_DUTIES.

  Bullet 1 : Power BI / DAX dashboard bullet with business impact metric  
  Bullet 2 : SQL/Excel data pipeline or ETL bullet with efficiency gain  
  Bullet 3 : Collaboration bullet (cross-functional or management-facing)  
  Bullet 4 : Reflection or process improvement bullet

% \=====================================================  
% LAYER 8 — COMMUNITY & LEADERSHIP DESCRIPTIONS  
% \=====================================================

Tailor each to the extracted JD context. One sentence each.

  Quackathon 2025 Participant  
    → link to: rapid prototyping, team problem-solving, or a JD-relevant tech area

  Friends International, Dundee Chapter  
    → link to: cross-cultural communication, stakeholder engagement, or community impact

  Peer Mentor for Coding Challenges  
    → link to: knowledge sharing, technical communication, or mentorship relevant to the role

% \=====================================================  
% LAYER 9 — EXTENDED SKILL BANK \+ ATS BONUS SKILLS  
% \=====================================================

PART A — EXTENDED SKILL BANK (Use ONLY if skill appears in JD)

  Category    | Skills  
  \------------|---------------------------------------------------------------  
  Languages   | C++, HTML, CSS  
  AI/ML       | LlamaIndex, Transformers, AI Agents, NLP/LLMs  
  Cloud       | Azure (AZ-900 certified), Azure AI (OpenAI, ML), AWS SageMaker, AWS Bedrock, GCP Vertex AI  
  Frameworks  | Django, Flask  
  Practices   | Experiment Design, SDLC, Interactive AI Applications, Data Pre-processing of unstructured data

PART B — ATS BONUS SKILLS (Company-Contextual Extras)

  PURPOSE: Add 2–4 skills that are NOT in the JD but are highly relevant  
  to the company's tech stack, industry, or team context. These give the  
  resume a "this person already knows our world" signal without cluttering  
  the JD-match layer.

  RULES:  
    1\. Infer bonus skills from: company name, industry, team description,  
       product context, or "nice to have" mentions in the JD.  
    2\. Maximum 4 bonus skills per resume. Minimum 2\.  
    3\. Place bonus skills ONLY in the Technical Skills table, never in  
       the header tagline or Professional Summary.  
    4\. Bonus skills go at the END of their row (after JD-matched skills),  
       separated by a pipe. Never lead with them.  
    5\. The candidate must genuinely have the skill (it must exist in the  
       CORE\_SKILLS or EXTENDED\_SKILLS bank, or in project/education context).  
    6\. Never add a bonus skill the candidate cannot defend in an interview.

  INFERENCE EXAMPLES:  
    Company: Expedia (travel, e-commerce)  
      → Bonus: Google Analytics, REST APIs, Agile/Scrum, Data Storytelling  
    Company: JP Morgan (banking, FinTech)  
      → Bonus: Data Governance, Risk Modelling, Time Series, Compliance Reporting  
    Company: NHS / Gov (public sector, health)  
      → Bonus: Data Ethics, Accessibility, GDPR, Public Sector Frameworks  
    Company: Startup (SaaS, early-stage)  
      → Bonus: Rapid Prototyping, MVP Development, Product Analytics, Growth Metrics

  OUTPUT FORMAT in Technical Skills table:  
    \\textbf{Practices:} & EDA | A/B Testing | \[JD skills...\] | \[Bonus 1\] | \[Bonus 2\] \\\\

  LABELLING: Do NOT label bonus skills differently in the output.  
  They should blend seamlessly into the skills table. The distinction  
  is internal to the prompt logic only.

% \=====================================================  
% LAYER 10 — FULL LATEX TEMPLATE  
% \=====================================================

\\documentclass\[11pt,a4paper\]{article}

% \============ PACKAGES \============  
\\usepackage\[margin=0.35in\]{geometry}  
\\usepackage{enumitem}  
\\usepackage{hyperref}  
\\usepackage{xcolor}  
\\usepackage{fontspec}  
\\usepackage{titlesec}  
\\usepackage{multicol}  
\\usepackage{tabularx}

% \============ FONT \============  
\\setmainfont{Arial}\[  
    BoldFont=Arial Bold,  
    ItalicFont=Arial Italic,  
    BoldItalicFont=Arial Bold Italic  
\]

% \============ COLORS & LINKS \============  
\\definecolor{linkblue}{RGB}{0, 102, 204}  
\\definecolor{sectionblue}{RGB}{0, 51, 102}  
\\hypersetup{  
    colorlinks=true,  
    linkcolor=linkblue,  
    urlcolor=linkblue,  
    citecolor=linkblue,  
    pdfborder={0 0 0},  
    linkbordercolor={0 0 0},  
    urlbordercolor={0 0 0}  
}  
\\newcommand{\\skylink}\[2\]{\\textit{\\textcolor{linkblue}{\\underline{\\href{\#1}{\#2}}}}}

% \============ CUSTOM COMMANDS \============  
\\newcommand{\\name}\[1\]{\\centerline{\\Huge\\textbf{\#1}}\\vspace{0.3em}}  
\\newcommand{\\address}\[1\]{\\centerline{\\normalsize{\#1}}}

% \============ SECTION FORMATTING \============  
\\titleformat{\\section}{\\large\\bfseries\\color{sectionblue}}{}{0em}{}\[\\titlerule\]  
\\titlespacing{\\section}{0pt}{0.5em}{0.3em}

% \============ LIST FORMATTING \============  
\\setlist\[itemize\]{noitemsep,topsep=2pt,parsep=0pt,leftmargin=\*}

% \============ DOCUMENT \============  
\\begin{document}  
\\pagestyle{empty}

% \---- HEADER \----  
\\name{Yash B}  
\\address{MSc Computer Science (UOD) | \[YEARS\_EXP\] YOE | \[ROLE\_TITLE\] | \[Skill1\] | \[Skill2\] | \[Skill3\] | \[Skill4\]}  
\\address{\[LOCATION\], UK | 07909445288 | \\skylink{mailto:bishnoiyash274@gmail.com}{Email} | \\skylink{https://linkedin.com/in/yash-bishnoi-2ab36a1a5}{LinkedIn} | \\skylink{https://github.com/yashb98}{GitHub} | \\skylink{https://yashbishnoi.io}{Portfolio}}

\\vspace{0.5em}

% \---- PROFESSIONAL SUMMARY \----  
\\section\*{Professional Summary}  
\[Write per Layer 5 / Step 4 rules — 3 sentences, professional tone, role \+ industry \+ top skills \+ soft skill\]

\\vspace{0.5em}

% \---- EDUCATION \----  
\\section\*{Education}

\\noindent\\textbf{MSc Computer Science}, University of Dundee \\hfill Jan 2025 \-- Jan 2026\\\\  
\\textbf{Dissertation:} Deep Learning for facial 3D Reconstruction \- Simulator\\\\  
\\textbf{Core Modules:} Machine Learning | Advanced Programming Techniques | Design Methods | Software Engineering | Software Development | Web Development | Database Systems

\\vspace{0.3em}

\\noindent\\textbf{MBA (Finance)}, JECRC University \\hfill 2019 \-- 2021\\\\  
CGPA: 8.21/10

\\vspace{0.5em}

% \---- TECHNICAL SKILLS \----  
\\section\*{Technical Skills}

\\noindent\\begin{tabular}{@{}p{2.8cm} p{14.4cm}@{}}  
\\textbf{Languages:} & Python | JavaScript | SQL | \[+ HTML | CSS | C++ only if in JD\] \\\\  
\\textbf{AI/ML:} & PyTorch | TensorFlow | Scikit-learn | Hugging Face | LangChain | Pandas | NumPy | \[+ LlamaIndex | Transformers | AI Agents | NLP/LLMs only if in JD\] \\\\  
\\textbf{Cloud/Tools:} & Docker | Kubernetes | GCP | AWS | Azure | FastAPI | Git | GitHub | MLflow | CI/CD | MLOps | \[+ Azure AI | SageMaker | Bedrock | GCP Vertex AI | Django | Flask only if in JD\] \\\\  
\\textbf{Practices:} & EDA | Documentation | Cross-functional Collaboration | \[+ Experiment Design | SDLC | Data Pre-processing | Interactive AI Applications only if in JD\] \\\\  
\\end{tabular}

\\vspace{0.5em}

% \---- PROJECTS \----  
\\section\*{Projects}

% PROJECT 1 — 3 bullets, pipes in title  
\\noindent\\textbf{1. Velox AI \- Enterprise AI Voice Agent Platform | Python | FastAPI | Docker | GCP} \\hfill \\skylink{https://github.com/yashb98}{(Link)}  
\\begin{itemize}\[noitemsep,topsep=1pt,parsep=0pt,leftmargin=\*\]  
    \\item \[Bullet — embed JD Skill1 or Skill2 with metric\]  
    \\item \[Bullet — technical detail, contraction required here OR in another bullet this project\]  
    \\item \[Bullet — imperfection phrase if used here, or outcome-focused\]  
\\end{itemize}

\\vspace{0.3em}

% PROJECT 2 — 5 bullets, commas in title  
\\noindent\\textbf{2. Cloud Sentinel \- AI Powered Cloud Security Platform with Python, React, Docker, Redis, Pinecone} \\hfill \\skylink{https://github.com/yashb98}{(Link)}  
\\begin{itemize}\[noitemsep,topsep=1pt,parsep=0pt,leftmargin=\*\]  
    \\item \[Architecture/infra bullet\]  
    \\item \[AI/ML pipeline bullet with metric\]  
    \\item \[Tooling/automation bullet with \\% improvement\]  
    \\item \[Stakeholder bullet — communication or feedback loop\]  
    \\item \[Reflection bullet — imperfection phrase or lesson learned\]  
\\end{itemize}

\\vspace{0.3em}

% PROJECT 3 — 4 bullets, no skills in title (catch-all)  
\\noindent\\textbf{3. 90 Days Machine learning} \\hfill \\skylink{https://github.com/yashb98}{(Link)}  
\\begin{itemize}\[noitemsep,topsep=1pt,parsep=0pt,leftmargin=\*\]  
    \\item \[Breadth bullet — number-led, list core tools, embed remaining JD skills naturally\]  
    \\item \[Evaluation/pipeline bullet — embed experiment design, data pre-processing if in JD\]  
    \\item \[Deployment bullet — Docker, K8s, CI/CD, MLflow, MLOps — only what's in JD\]  
    \\item \[Reflection bullet — SDLC, collaboration, or cross-functional lesson\]  
\\end{itemize}

\\vspace{0.3em}

% PROJECT 4 — 3 bullets, "with" in title  
\\noindent\\textbf{4. Deep Learning for Facial 3D Reconstructions with PyTorch and Computer Vision} \\hfill \\skylink{https://github.com/yashb98}{(Link)}  
\\begin{itemize}\[noitemsep,topsep=1pt,parsep=0pt,leftmargin=\*\]  
    \\item \[Model architecture bullet with SSIM metric — keep\]  
    \\item \[Data generation bullet with 10,000+ number — keep\]  
    \\item \[Documentation/communication bullet — adapt soft skill to JD\]  
\\end{itemize}

\\vspace{0.5em}

% \---- EXPERIENCE \----  
\\section\*{Experience}

\\noindent\\textbf{Team Leader} \\hfill Apr 2025 \-- Present\\\\  
Co-op  
\\begin{itemize}\[noitemsep,topsep=1pt,parsep=0pt,leftmargin=\*\]  
    \\item \[Route via Layer 7 / ROLE 1 domain routing — Standard pattern\]  
    \\item \[Route via Layer 7 / ROLE 1 domain routing — Outcome-first pattern\]  
    \\item \[Route via Layer 7 / ROLE 1 domain routing — Short punch pattern\]  
    \\item \[Route via Layer 7 / ROLE 1 domain routing — Stakeholder pattern\]  
\\end{itemize}

\\vspace{0.3em}

\\noindent\\textbf{Market Research Analyst} \\hfill Jul 2021 \-- Sep 2024\\\\  
Nidhi Herbal  
\\begin{itemize}\[noitemsep,topsep=1pt,parsep=0pt,leftmargin=\*\]  
    \\item \[Power BI / DAX dashboard bullet with business impact metric\]  
    \\item \[SQL/Excel data pipeline or ETL bullet with efficiency gain\]  
    \\item \[Collaboration bullet — cross-functional or management-facing\]  
    \\item \[Reflection or process improvement bullet\]  
\\end{itemize}

\\vspace{0.5em}

% \---- CERTIFICATIONS \----  
\\section\*{Certifications}

\\noindent\\textbf{1. IBM Machine Learning} \\hfill July 2023 \-- \\skylink{https://www.coursera.org/account/accomplishments/specialization/certificate/SL9P2Q6Z43JP}{Verify}

\\vspace{0.2em}

\\noindent\\textbf{2. SQL Essential Learning} \\hfill September 2023 \-- \\skylink{https://www.linkedin.com/learning/certificates/df5df7f2f991831af1bf1429e9c267ed342ae6db0f951f55cf7b6b7ce72e1f2b}{Verify}

\\vspace{0.15em}

\\noindent\\textbf{3. Feature Engineering} \\hfill July 2023 \-- \\skylink{https://www.kaggle.com/learn/certification/yashbishnoi98/feature-engineering}{Verify}

\\vspace{0.25em}

\\noindent\\textbf{4. Data Cleaning} \\hfill July 2023 \-- \\skylink{https://www.kaggle.com/learn/certification/yashbishnoi98/data-cleaning}{Verify}

\\vspace{0.2em}

\\noindent\\textbf{5. Exploratory Data Analysis for Machine Learning} \\hfill July 2023 \-- \\skylink{https://www.coursera.org/account/accomplishments/certificate/LPEX59KAWX8X}{Verify}

\\vspace{0.3em}

\\noindent\\textbf{6. Deep Learning and Reinforcement Learning} \\hfill July 2023 \-- \\skylink{https://www.coursera.org/account/accomplishments/certificate/S2MJH2ZQ8WF4}{Verify}

\\vspace{0.15em}

\\noindent\\textbf{7. Introduction to Model Context Protocol} \\hfill Feb 2026 \-- \\skylink{http://verify.skilljar.com/c/nn63q5jje52u}{Verify}

\\vspace{0.15em}

\\noindent\\textbf{8. Microsoft Azure Fundamentals (AZ-900)} \\hfill Mar 2026 \-- \[Verify link TBC or "Expected Mar 2026" if pre-exam\]

\\vspace{0.5em}

% \---- COMMUNITY & LEADERSHIP \----  
\\section\*{Community and Leadership}

\\noindent\\textbf{1. Quackathon 2025 Participant.} \[One sentence — link to JD tech area or rapid prototyping\]

\\vspace{0.3em}

\\noindent\\textbf{2. Friends International, Dundee Chapter.} \[One sentence — link to stakeholder comms or cross-cultural collaboration\]

\\vspace{0.3em}

\\noindent\\textbf{3. Peer Mentor for Coding Challenges.} \[One sentence — link to knowledge sharing or technical communication\]

\\vspace{0.5em}

% \---- REFERENCES \----  
\\section\*{References}  
\\vspace{0.5em}  
\\centerline{\\textbf{\\textit{\\underline{\\textcolor{linkblue}{\\skylink{mailto:bishnoiyash274@gmail.com}{Available upon request}}}}}}

\\end{document}

% \=====================================================  
% LAYER 11 — PRE-OUTPUT VERIFICATION CHECKLIST  
% \=====================================================

STRUCTURE CHECKS:  
  \[ \] EXTRACTION block printed before LaTeX  
  \[ \] Header tagline uses top 4 skills from SKILLS\_LIST in JD order  
  \[ \] Professional Summary: professional tone, no casual phrases  
  \[ \] Professional Summary: exactly 3 sentences, each under 25 words  
  \[ \] Professional Summary renders within 4 lines at 11pt Arial  
  \[ \] Professional Summary: 3–5 key terms bolded (role title, top skills, industry)  
  \[ \] Professional Summary: no soft skills, verbs, or generic words bolded  
  \[ \] Strict 2-page limit

SKILLS TABLE CHECKS:  
  \[ \] 2–4 ATS bonus skills added, placed at END of their respective rows  
  \[ \] Bonus skills are genuinely defensible (exist in candidate's actual experience)  
  \[ \] Bonus skills are NOT in the header tagline or Professional Summary  
  \[ \] Max 8 items per skills row maintained even with bonus additions

BULLET CHECKS:  
  \[ \] Every bullet fits within 1–1.5 lines at 11pt Arial  
  \[ \] At least one bullet starts with a number  
  \[ \] At least one bullet is a fragment (no leading verb)  
  \[ \] Each project section has at least one contraction  
  \[ \] Exactly 3 imperfection phrases used; no repeats  
  \[ \] Max 2 bold keywords per bullet; max 60% bold density  
  \[ \] Project 3 skills look natural, not keyword-stuffed

EXPERIENCE GROUNDING CHECKS:  
  \[ \] All 4 bullet patterns present in each role  
  \[ \] Every Co-op bullet maps to at least one GROUND\_TRUTH\_DUTY  
  \[ \] Co-op bullets use ADAPTATION\_RULE matching the target JD domain  
  \[ \] Nidhi Herbal bullets reference Power BI, DAX, and/or SQL/Excel, and Python, openpyxl, and low level Data Science   
  \[ \] No fabricated duties in either role

PROJECT GROUNDING CHECKS:  
  \[ \] Cloud Sentinel bullets reference only tech from PROJECT\_2\_GROUND\_TRUTH.ACTUAL\_TECH\_STACK  
  \[ \] Cloud Sentinel capabilities described match ACTUAL\_CAPABILITIES (no fabrication)  
  \[ \] Project keywords use PROJECT\_2\_GROUND\_TRUTH.KEYWORD\_BANK for JD translation

LATEX / LINK CHECKS:  
  \[ \] \-- appears ONLY in date ranges (literal scan of full output)  
  \[ \] All links underlined in linkblue, no border boxes  
  \[ \] All certification URLs match the fixed list exactly

FIXED ELEMENT CHECKS:  
  \[ \] No changes to project titles, cert titles, community titles, job titles, dissertation, or core modules  
  \[ \] Extended skills only appear if confirmed in EXTRACTED block

HONESTY GATE CHECK:  
  \[ \] No JD keyword appears in Experience that cannot be traced to a GROUND\_TRUTH\_DUTY  
  \[ \] Skills that failed the honesty gate are routed to Projects, not Experience

% \=====================================================  
% COMPILATION  
% \=====================================================

xelatex file.tex  
xelatex file.tex   % Run twice for correct layout

Font: Arial 11pt (fallback: any sans-serif). Margins: 0.35in all sides.

