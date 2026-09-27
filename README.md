# Job Search Swarm

A personal, multi-agent job-search assistant for anyone looking for work in Australia.

Upload your resume and describe the jobs you want. The app then:

- finds fresh postings every couple of hours and scores each one against your background;
- writes a tailored, fact-checked resume and cover letter for the best matches;
- keeps a daily list of newly funded Australian startups in your field, with in-depth profiles;
- drafts personal outreach to the people at the startups you shortlist;
- reads application forms, drafts every answer and fills the form in for you.

It runs on your own computer (Windows, macOS or Linux) with your own Anthropic API key, uses free job and
news sources, and stops at a spending cap you set. **Nothing is ever sent, submitted or published
without you.** You approve every resume, send every message and click every Submit button yourself.

---

## Contents

1. [How it works at a glance](#how-it-works-at-a-glance)
2. [Guarantees](#guarantees)
3. [Installation](#installation)
4. [First-run setup](#first-run-setup)
5. [Day-to-day use](#day-to-day-use)
6. [The dashboard, tab by tab](#the-dashboard-tab-by-tab)
7. [How each agent works](#how-each-agent-works)
8. [Costs and the budget](#costs-and-the-budget)
9. [Configuration reference](#configuration-reference)
10. [Command-line reference](#command-line-reference)
11. [Scheduling](#scheduling)
12. [Your data and privacy](#your-data-and-privacy)
13. [Architecture](#architecture)
14. [Development and testing](#development-and-testing)
15. [Troubleshooting](#troubleshooting)
16. [Limitations](#limitations)

---

## How it works at a glance

```
                ┌──────────────────────────────────────────────────────────────────────────┐
                │  YOU: profile (source of truth) + what you're looking for + approvals    │
                └──────────────────────────────────────────────────────────────────────────┘
                        │                                              ▲ review, edit, approve,
                        ▼                                              │ send, submit
 every 2h   ┌───────────────────┐   shortlisted   ┌──────────────────┐ │   approved   ┌──────────────┐
 ─────────▶ │ 1. Job Scout      │ ──────────────▶ │ 2. Resume Tailor │─┼────────────▶ │ 5. Applica-  │
            │ fetch · filter ·  │                 │ tailor · fact-   │ │              │ tions        │
            │ dedupe · score    │                 │ check · humanize │ │              │ read form ·  │
            └───────────────────┘                 │ · render PDF     │ │              │ draft · pre- │
                     ▲ new company job boards     └──────────────────┘ │              │ fill         │
                     │                                                 │              └──────────────┘
 06:00      ┌───────────────────┐   shortlisted   ┌──────────────────┐ │
 ─────────▶ │ 3. Startup Intel  │ ──────────────▶ │ 4. Outreach      │─┘
            │ news · extract ·  │                 │ find people ·    │
            │ enrich · profile  │                 │ draft messages   │
            └───────────────────┘                 └──────────────────┘

 All agents share one SQLite database, one budget-guarded Claude client and one dashboard.
```

| Agent | Input | Output | Models |
|-|-|-|-|
| **Job Scout** | Company job boards, Adzuna, Jooble | Scored, deduplicated jobs; shortlist | Sonnet |
| **Resume Tailor** | Shortlisted job + your profile | One-page resume and cover letter PDFs awaiting approval | Opus writes, Sonnet checks and humanizes |
| **Startup Intel** | Startup funding news (RSS, Google News) | Daily list of funded startups with in-depth profiles | Sonnet extracts, Opus profiles |
| **Outreach** | Startups you shortlist | Ranked contact plan and messages for you to send | Opus |
| **Applications** | Approved resumes | Drafted, fact-checked answers; form pre-filled in your browser | Sonnet |

---

## Guarantees

These are enforced in code, not left to a prompt:

- **Nothing goes out without you.** The app never submits applications, sends emails or LinkedIn messages,
  or posts anything. It prepares drafts and pre-fills forms; you click Send or Submit.
- **Truthfulness.** Your profile is the only source of facts. Tailored resume bullets must cite the profile
  facts they came from. A fact checker (rules first, then Claude) replaces or drops any line that adds,
  inflates or changes a fact or number, and **repairs never add content**: a failed line falls back to your
  own wording. Drafted outreach and application answers are checked the same way. Unknowns such as salary
  or "do you know anyone here?" become *Needs you* questions instead of guesses.
- **Budget caps.** Every Claude call is checked against its worst-case cost before it runs. A run that would
  exceed your daily or monthly cap stops cleanly instead.
- **Your data stays local.** Your profile, preferences, database, resumes and keys live in git-ignored
  files on your computer.
- **No scraping of LinkedIn or Seek.** Job data comes from public job-board APIs and free aggregator APIs.
  For LinkedIn you get search links to open yourself. CAPTCHAs are never bypassed.

---

## Installation

### Requirements

- Python 3.11 or newer
- An [Anthropic API key](https://console.anthropic.com/settings/keys)
- A Chromium-based browser for PDF rendering and form filling. Microsoft Edge or Google Chrome is used if
  installed; otherwise Playwright's bundled Chromium (installed below).
- Optional, free: [Adzuna](https://developer.adzuna.com/) and [Jooble](https://jooble.org/api/about) API
  keys, which add jobs from across Australian job boards.

### Windows (PowerShell)

```powershell
git clone <this repo> job-search; cd job-search
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m jobsearch dashboard
```

### macOS

```bash
git clone <this repo> job-search && cd job-search
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium     # skip if Edge or Chrome is installed
.venv/bin/python -m jobsearch dashboard
```

### Linux (Debian/Ubuntu shown)

```bash
sudo apt install python3-venv fonts-crosextra-carlito   # venv support + a Calibri-compatible resume font
git clone <this repo> job-search && cd job-search
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install --with-deps chromium
.venv/bin/python -m jobsearch dashboard
```

The dashboard opens at <http://localhost:8501>. Use `--port` to change the port and `--headless` to stop it
opening a browser tab.

---

## First-run setup

On a new install the dashboard shows a three-step checklist, and the **Run** buttons stay disabled until
it's complete.

### 1. Add your API key

**Job search → API keys.** Paste your Anthropic key, plus optional Adzuna and Jooble keys. They're saved to
`.env` on your computer. If your key belongs to a whole organisation rather than one workspace, also add
your workspace ID (it starts with `wrkspc_`; find it in the Anthropic Console under Settings → Workspaces).

### 2. Build your profile

**My profile.** Upload your resume (PDF or DOCX) or paste it, then click *Read my resume into a profile*.
Claude converts it into a structured profile: every bullet becomes a separate fact, and wording and numbers
are kept exactly. Nothing is added. Review what it found, save it, then use the editor to fill in:

- **Contact details** and the one-line **headline** under your name.
- **Work rights:** citizen, permanent resident or visa holder, visa expiry, sponsorship needs, and whether you
  can hold a security clearance. Job filtering and application answers follow these, e.g. clearance-only
  roles are dropped for visa holders.
- **Experience and projects:** each has a title, dates, tools and a list of facts. Add anything true that
  your resume leaves out: numbers, scope, tools, outcomes. More facts give better tailored resumes.
- **Skills**, **education**, certifications and awards.
- **Answers for application forms:** name as it should appear on forms, salary expectation, notice period,
  earliest start, relocation. Anything left empty is asked per application.

### 3. Describe what you're looking for

**Job search.** Write, in plain English, the jobs you want: roles, level, industries, locations and what
to avoid. Click *Suggest search settings* and Claude proposes:

- target job titles;
- search phrases for the job APIs;
- title keywords a relevant job must match, and ones that rule a job out;
- preferred cities and whether remote is fine;
- a research focus for the funded-startups list (e.g. "AI", "fintech", "health tech").

Review and adjust them, then save. You can also build the **company watchlist** here: type a company name
and the app finds its public job board (Greenhouse, Lever, Ashby, Workable or SmartRecruiters) so every run
checks it directly.

Then click **Run scout now** in the sidebar, and consider [scheduling](#scheduling) the daily runs.

---

## Day-to-day use

With scheduling on, a typical day looks like this:

| When | What happens | What you do |
|-|-|-|
| 06:00 | Startup Intel reads funding news, profiles the best new startups, and drafts outreach for any you've shortlisted | Skim **Startups**; shortlist promising ones |
| Every 2h, 07:00–23:00 | Job Scout fetches and scores new jobs, then tailors resumes for the top new matches | — |
| Whenever you like | — | **Approvals:** review each resume, close gaps, approve or regenerate |
| | Approving a resume prepares its application | **Applications:** answer any *Needs you* questions, preview, open pre-filled, submit yourself, *Mark applied* |
| | | **Outreach:** review drafts, send from LinkedIn or Gmail yourself, *Mark sent*; follow-ups appear when due |

---

## The dashboard, tab by tab

The sidebar shows today's and this month's spend against your caps, plus **Run scout now** and **Run
startup intel now**.

| Tab | What it's for |
|-|-|
| **Jobs** | Every job found, sorted by freshness then fit score. Filter by status, freshness tier (A: under 24h, B: 1–7 days, C: 7–30 days), minimum score and city. Select a job for the scorer's summary, matched strengths, gaps, required skills and keywords, and buttons to shortlist, dismiss, re-score or tailor now. |
| **Approvals** | Each tailored resume with a page preview, why it was tailored that way, keyword coverage, what the fact checker changed (with the original wording), what the humanizer changed, the cover letter and the cost. Buttons: download PDFs, **Approve** (also prepares the application), **Regenerate** (re-tailors immediately), **Reject**. **Close the gaps** turns the job's gaps into profile facts you confirm, then regenerates. Also lists shortlisted jobs waiting to be tailored (**Tailor now**) and approved ones. |
| **Applications** | One card per prepared application: every form question and drafted answer, editable, with *Needs you* questions first. **Preview fill** screenshots the real form filled in (never submitted); **Open pre-filled in browser** gives you a visible window to check and submit; **Mark applied** records it. Guided applications show a copy-paste kit. |
| **Startups** | The funded-startups list (90-day rolling window, "new today" marker, sorted by relevance to you). Select one for its full profile: vision, product, market, funding, how its focus technology fits the business, hiring signals, named people, relevance and an outreach angle. **Shortlist for outreach**, **Draft outreach**, **Dismiss**, **Re-profile**. |
| **Outreach** | Contact plans per startup: who to contact first and why, then for each person a connection note, LinkedIn message, email and follow-up in copy boxes, with **Find on LinkedIn** and **Open Gmail draft** links. Mark each **sent**, **replied** or **skipped**; follow-ups show when due. |
| **My profile** | Resume import and the full profile editor (see [First-run setup](#2-build-your-profile)). |
| **Job search** | Search settings from a plain-English description, the company watchlist, budget caps and API keys. |
| **Spend** | Daily cost by agent over the last 30 days, and totals per agent and model (calls, tokens, cost). |
| **Runs** | Every scout, tailor, intel and outreach run with its statistics and any error. |

---

## How each agent works

### 1. Job Scout

1. **Fetch.** Pulls postings from the public job-board APIs of every company in your watchlist (Greenhouse,
   Lever, Ashby, Workable, SmartRecruiters) and, if keys are set, from Adzuna and Jooble using your search
   phrases.
2. **Filter (free, no tokens).** Drops postings that are:
   - older than `max_age_days`;
   - outside Australia (it tells "Melbourne, VIC" from "Melbourne, FL");
   - titled with nothing from your include keywords, or with an exclude keyword. Companies marked
     *any role* in the watchlist accept any title matching `ai_native_title_include`.
   - asking for something your work rights can't meet, such as a security clearance for a visa holder.

   Usually 95%+ of postings are dropped here.
3. **Dedupe.** The same job from several sources becomes one row: same company (normalised) with the same
   external ID, or a near-identical title in the same city. The employer's own job-board link wins over
   an aggregator's, and the earliest posting date is kept.
4. **Score, newest first.** Sonnet reads each new job against your profile and preferences and returns a fit
   score (0–100), a recommendation, seniority fit, a work-rights verdict, must-have skills, keywords to use,
   your matching strengths, your gaps and a two-sentence summary. Jobs under 24 hours old are always scored
   before older ones. Your profile sits in a cached prompt prefix, so each job costs well under a cent.
5. **Shortlist.** A job is shortlisted when its score is at least `shortlist_threshold`, its work rights are
   compatible and the recommendation isn't *skip*. Up to `tailoring.max_per_run` new shortlisted jobs are
   then tailored automatically.

### 2. Resume Tailor

1. **Fact catalogue.** Your profile becomes a list of facts with IDs (`acme.1`, `acme.2`, …, one per bullet).
2. **Tailor (Opus).** Selects and rewrites facts for the job. Every bullet, the summary and every
   cover-letter paragraph must cite the fact IDs it came from. It mirrors the ad's wording only where a fact
   supports it, copies numbers exactly and lists only skills that are in your profile. It also writes a
   cover letter and notes on why it emphasised what it did.
3. **Fact check.** Free rule-based checks first:
   - cited facts exist and belong to the right job or project;
   - every number appears in the cited source;
   - every listed skill is in your profile;
   - every real job is present.

   Then Sonnet judges each remaining claim against its sources. A failing bullet is replaced by your original
   fact wording or dropped; a failing summary reverts to your own summary.
4. **Humanize.** A Sonnet editing pass, based on the humanize-writing guide, removes AI-sounding patterns:
   stock vocabulary ("leverage", "robust", "spearheaded"), inflated significance, "serves as", decorative
   "-ing" endings, em dashes, filler, stock transitions and monotonous rhythm. The cover letter gets a plain
   first-person voice. Every line the humanizer changes is fact-checked again, and a line that drifted
   reverts to the fact-checked version.
5. **Render.** HTML is printed to PDF by a headless browser in a clean one-page A4 layout. If it runs over a
   page, the lowest-priority bullets are trimmed until it fits. A cover-letter PDF and a preview image are
   made too.
6. **Review.** The result waits in **Approvals**.

**Close the gaps.** Each resume card lists the job's gaps: keywords the resume doesn't cover and gaps the
scorer found (notes about the ad itself are filtered out). For each gap you really have, choose where it
belongs (a skills group, a job or a project) and write one true sentence. It's added to your profile, so
every future resume, score and message can use it, and the resume is regenerated. You can also require
keywords already in your profile to appear.

### 3. Startup Intel

1. **Collect (free).** Reads RSS from Startup Daily, SmartCompany, StartupSmart and Crunchbase News, plus
   Google News searches built from your research focus. Each article is recorded, so it's processed only
   once.
2. **Filter (free).** Keeps articles about funding that mention your focus and Australia. Weekly funding
   round-ups always pass.
3. **Extract (Sonnet).** Several articles per call become structured funding events: company, city, round,
   amount, investors, date and how central your focus is to the company. Only Australian companies where
   it's core or significant are kept.
4. **Enrich (free).** Finds the company's website (rejecting parked and placeholder pages), careers-page
   text and public job board. Companies with a job board are added to your watchlist automatically, so the
   Job Scout starts checking their roles.
5. **Profile (Opus).** Each day the most promising new startups (`intel.max_profiles_per_day`, favouring
   focus-centric, preferred-city, currently hiring and recently funded) get an in-depth profile. It covers
   vision, product, customers, business model, funding, a detailed analysis of how the focus technology fits
   their product (evidence kept separate from inference), likely roles, hiring signals, people named in the
   sources (never invented), relevance to you and an outreach angle.
6. **Report.** Shown in the **Startups** tab and written to `data/reports/intel_YYYY-MM-DD.md`.

### 4. Outreach

1. **Trigger.** Shortlist a startup and click *Draft outreach*, or let the 06:00 run draft for shortlisted
   startups.
2. **Find people (free, plus one small Sonnet call).** Founders and leaders named in the news, people on the
   company's own about and team pages, and email addresses the company publishes. An email *format* is
   inferred only when a published address proves it, and it's labelled unverified. LinkedIn is never
   fetched.
3. **Draft (Opus).** A ranked plan: who to contact first, who next, and what to avoid (e.g. don't message
   every co-founder at once). For each person it writes a LinkedIn connection note (within the length limit),
   a LinkedIn message, an email and a follow-up, each with a different angle. Where no suitable person is
   named, it suggests a role to look up (e.g. Head of Engineering) with a LinkedIn search link.
4. **Checks.** Flags over-length notes, numbers not found in your profile or the startup's sources, and
   names that weren't discovered.
5. **You send.** Copy the text, or use *Open Gmail draft* for a pre-filled compose window. Mark each message
   sent; follow-ups appear after `follow_up_days`.

### 5. Applications

1. **Prepare** (automatic when you approve a resume). Reads the job's application form from the job board's
   public form definition (Greenhouse, Lever, Ashby) with every question, whether it's required and its
   options. Other sites (Adzuna links, Workday, Seek and so on) get a *guided* kit of the usual screening
   questions.
2. **Answer.**
   - Standard fields (name, contact details, location, LinkedIn, resume and cover-letter files) come from
     your profile.
   - Demographic questions default to "decline to self-identify".
   - Everything else is one Sonnet call. Choice questions must use one of the form's own options.
   - Drafted prose is fact-checked against your whole profile.
   - Unknown or unsupported answers, and questions only you can answer, become **Needs you**.
3. **Review** in **Applications**. The fill buttons stay disabled until every *Needs you* question is
   resolved.
4. **Preview fill** fills the real form in a hidden browser, checks every value reads back correctly and
   shows a full-page screenshot. It never submits.
5. **Open pre-filled in browser** opens a visible window with everything filled in. You check it, solve any
   CAPTCHA, click **Submit** yourself, close the window and click **Mark applied**.

---

## Costs and the budget

### What things cost

Measured averages from real runs (USD):

| Step | Model | Typical cost |
|-|-|-|
| Score one job | Sonnet | $0.006–0.015 (≈ $0.007) |
| Tailored resume + cover letter (tailor, fact check, humanize) | Opus + Sonnet | $0.10–0.14 |
| Extract a batch of funding articles | Sonnet | $0.002–0.06 |
| Profile one startup | Opus | ≈ $0.10 |
| Outreach plan for one startup | Opus | ≈ $0.11 |
| Prepare one application | Sonnet | ≈ $0.02–0.04 |
| Read a resume into a profile (one-off) | Opus | ≈ $0.10 |
| Suggest search settings | Sonnet | ≈ $0.01 |
| Humanize an existing resume | Sonnet | ≈ $0.05 |

With the defaults, a full day (≈30 jobs scored, 2–3 resumes, 2–3 startup profiles, one outreach plan)
costs about $1–1.50.

### How the caps work

`budget` in `config/preferences.yaml` (editable in **Job search → Budget**):

- `daily_usd` and `monthly_usd` are hard caps (defaults $1.50 and $40).
- `per_agent_daily` gives each agent a share of the daily cap (default: scout $0.30, tailor $0.60,
  intel $0.40, outreach $0.20), so one agent can't starve the others on scheduled runs.
- Before every call, its **worst-case** cost (the full `max_tokens` at output price, plus input) is
  checked against the agent's share, the daily cap and the monthly cap. If it doesn't fit, the run stops
  cleanly and records why. Actual spend therefore stays under the caps.
- Actions you start yourself (dashboard buttons, or CLI runs on one specific job or startup) may use
  whatever is left of the daily cap, ignoring agent shares.
- Every call's tokens and cost are recorded in the `llmusage` table and shown in **Spend**.

### Models

| Setting (`models:`) | Default | Used for |
|-|-|-|
| `tailoring` | `claude-opus-5` | Resume and cover letter; resume import |
| `research` | `claude-opus-5` | Startup profiles |
| `outreach` | `claude-opus-5` | Outreach plans |
| `verification` | `claude-sonnet-5` | Fact checks |
| `humanizer` | `claude-sonnet-5` | Humanizer pass (`claude-opus-5` for a heavier edit) |
| `default` | `claude-sonnet-5` | Scoring, extraction, application answers, search settings |

Opus calls opt into server-side refusal fallbacks and are billed at whichever model actually answered.
Cost-saving measures built in: prompt caching of stable prefixes, free filters before any LLM call, batching
several articles per extraction call, and low effort settings for simple steps.

---

## Configuration reference

All configuration lives in `config/` and `.env`, is git-ignored, and can be edited in the dashboard or by
hand. On first run each file is created from its template in `config/examples/`.

### `config/profile.yaml`: your source of truth

| Section | Contents |
|-|-|
| `identity` | name, headline, email, phone, location, linkedin, github, portfolio |
| `work_rights` | `status` (citizen / permanent_resident / visa), visa, visa_expiry, full_work_rights, requires_sponsorship_now, wants_sponsorship_later, can_hold_clearance, resume_line, notes |
| `summary` | Professional summary |
| `skills` | Group name → list of skills |
| `experience` | Entries with `id`, title, company, location, start, end, project, stack, `facts` |
| `projects` | Entries with `id`, name, dates, award, stack, `facts` |
| `education`, `certifications`, `awards` | Lists |
| `application` | Answers for forms: first/last name, phone, city, current employer and title, salary_expectation, notice_period, earliest_start, willing_to_relocate, pronouns, how_did_you_hear, `eeo_default` (decline / ask me) |

Any value that is exactly `TODO` is hidden from the agents and treated as unknown. Facts added through
*Close the gaps* go to `config/profile_additions.yaml` and are merged in automatically; saving from the
**My profile** editor folds them into `profile.yaml`.

### `config/preferences.yaml`: search and system settings

| Key | Meaning | Default |
|-|-|-|
| `search.looking_for` | Your description in your own words (the scorer reads it) | — |
| `search.target_roles` | Job titles you want | — |
| `search.avoid_roles` | Kinds of roles to score lower | — |
| `search.queries` | Phrases sent to Adzuna and Jooble | — |
| `search.title_include` | A relevant title matches one of these (keywords or regex) | — |
| `search.title_exclude` | Titles matching these are dropped | `intern` |
| `search.ai_native_title_include` | Titles accepted at *any role* watchlist companies | engineer, developer, analyst, … |
| `search.locations.preferred` / `.acceptable` / `.allow_remote_au` | Where you'll work | all major AU cities acceptable |
| `search.max_age_days` | Ignore older postings | 30 |
| `work_rights.hard_exclude_patterns` | Override the exclusions derived from your work rights | derived |
| `scoring.max_scores_per_run` | Scoring calls per run | 60 |
| `scoring.shortlist_threshold` | Fit score to shortlist | 70 |
| `tailoring.auto_after_scout` / `max_per_run` / `max_per_day` | Automatic tailoring limits | true / 3 / 4 |
| `tailoring.humanize` | Run the humanizer pass | true |
| `intel.topic` / `topic_keywords` / `google_news_queries` | Startup research focus | AI |
| `intel.lookback_days` / `max_profiles_per_day` | News window and daily Opus profiles | 45 / 3 |
| `outreach.max_startups_per_day` / `max_contacts_per_startup` / `max_role_targets` | Outreach limits | 2 / 3 / 2 |
| `outreach.connection_note_chars` | LinkedIn note limit (300 with Premium) | 200 |
| `outreach.follow_up_days` | When a follow-up becomes due | 7 |
| `budget.*` | See [Costs and the budget](#costs-and-the-budget) | $1.50/day, $40/month |
| `models.*` | See [Models](#models) | |
| `timezone` | Used for "today" in budgets and reports | Australia/Melbourne |

### `config/watchlist.yaml`: companies checked directly

```yaml
companies:
  - {name: Canva, ats: smartrecruiters, slug: canva}
  - {name: Lorikeet, ats: ashby, slug: lorikeet, ai_native: true}   # ai_native: any role qualifies
```

`ats` is one of `greenhouse`, `lever`, `ashby`, `workable`, `smartrecruiters`; `slug` is the company's ID on
that board. Startup Intel appends companies it discovers. The template ships with 37 Australian tech
and AI companies as a starting point.

### `.env`

| Variable | Required | Purpose |
|-|-|-|
| `ANTHROPIC_API_KEY` | yes | Claude API key |
| `ANTHROPIC_WORKSPACE_ID` | only for organisation-level keys | Workspace the requests are billed to |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | no | Adzuna job search (free tier) |
| `JOOBLE_API_KEY` | no | Jooble job search (free) |
| `JOBSEARCH_BROWSER` | no | Force `msedge`, `chrome` or `chromium` |

---

## Command-line reference

Windows paths are shown; on macOS and Linux use `.venv/bin/python`.

| Command | What it does |
|-|-|
| `python -m jobsearch dashboard [--port 8501] [--headless]` | Start the dashboard |
| `python -m jobsearch scout` | Fetch, filter, score, then tailor the top new matches |
| `python -m jobsearch scout --no-score` | Fetch and filter only (free) |
| `python -m jobsearch scout --limit N` | Score at most N jobs this run |
| `python -m jobsearch tailor [--limit N]` | Tailor shortlisted jobs, newest first |
| `python -m jobsearch tailor --job ID` | Tailor one specific job |
| `python -m jobsearch intel [--profiles N]` | Startup research, then outreach drafts for shortlisted startups |
| `python -m jobsearch intel --startup ID` | (Re)profile one startup |
| `python -m jobsearch outreach [--startup ID] [--limit N]` | Draft outreach (never sends) |
| `python -m jobsearch schedule show \| install \| remove` | Manage the daily schedule |
| `python -m jobsearch status` | Today's and this month's spend, and jobs by status |

---

## Scheduling

```bash
python -m jobsearch schedule show      # preview what would be installed
python -m jobsearch schedule install   # install
python -m jobsearch schedule remove    # undo
```

Two jobs are scheduled: **intel at 06:00** (followed by outreach drafts), and **scout every 2 hours from
07:00 to 23:00** (followed by tailoring).

| OS | Mechanism | Where |
|-|-|-|
| Windows | Task Scheduler (runs while you're logged in) | Tasks `JobSearch-Intel`, `JobSearch-Scout` |
| macOS | launchd agents | `~/Library/LaunchAgents/com.jobsearch-swarm.{intel,scout}.plist` |
| Linux | cron | Two lines in `crontab -l`, marked `# jobsearch-swarm` |

Scheduled runs log to `data/logs/` and appear in the **Runs** tab. Your computer needs to be on. If it was
asleep at a scheduled time, Task Scheduler and launchd run the missed job when it wakes; cron skips it.

---

## Your data and privacy

**Stored on your computer (all git-ignored):**

| Path | Contents |
|-|-|
| `config/profile.yaml`, `profile_additions.yaml` | Your profile |
| `config/preferences.yaml`, `watchlist.yaml` | Your settings |
| `.env` | API keys |
| `data/jobsearch.db` | SQLite database: jobs, resume versions, applications, startups, outreach, usage, runs |
| `data/resumes/` | Tailored PDFs, previews and form screenshots, one folder per job |
| `data/reports/` | Daily startup reports |
| `data/logs/` | Logs |

**Sent elsewhere:**

- **Anthropic** receives your profile, job ads, news articles and startup website text as part of the
  prompts. That's how the agents work, and it's governed by your Anthropic account's data policies.
- **Public job boards, aggregators and news sites** receive ordinary read-only requests (job listings,
  application-form definitions, RSS feeds, company web pages). No personal data is sent to them.
- **Employer application forms** receive your details only when you open a pre-filled form and submit it
  yourself. *Preview fill* types your answers into the real form in a hidden browser to take a screenshot,
  then closes it without submitting.

---

## Architecture

### Layout

```
jobsearch/
├── __main__.py        CLI entry point (python -m jobsearch …)
├── config.py          Loads and saves config/*.yaml and .env; first-run templates; profile additions
├── persona.py         Everything about *this* user the prompts need (name, city, work rights, roles)
├── llm.py             The only way to call Claude: budget guard, usage ledger, prompt caching,
│                      structured outputs, refusal fallbacks, escape clean-up
├── db.py              SQLModel tables and additive migrations
├── browser.py         Starts Edge → Chrome → Playwright Chromium on any OS
├── schedule.py        Task Scheduler / launchd / cron
├── onboarding.py      Resume → profile; description → search settings
├── scout/             Job Scout: sources/ (ATS boards, Adzuna, Jooble), filters, dedupe, scorer, pipeline
├── tailor/            Resume Tailor: facts (catalogue), tailor, verify, humanize, render, coverage, gaps, pipeline
├── intel/             Startup Intel: news, extract, enrich, profile, pipeline
├── outreach/          Outreach: people discovery, compose, pipeline
├── apply/             Applications: schema (form readers), answers, filler, pipeline
└── ui/                Dashboard tabs: My profile, Job search
dashboard/app.py       Streamlit dashboard
config/examples/       Templates copied to config/ on first run
scripts/               Windows Task Scheduler script
tests/                 Test suite, fixtures (fictional persona, mock application forms)
```

### Data model and lifecycles

| Table | Lifecycle |
|-|-|
| `job` | `new` → `scored` / `filtered_out` → `shortlisted` → `tailored` → `approved` → `applied` (or `dismissed`) |
| `resumeversion` | `pending_review` → `approved` / `rejected` / `superseded` |
| `application` | `needs_input` → `ready` → `applied` (modes: `prefill` or `guided`) |
| `newsitem` | Every article seen, so each is processed once |
| `startup` | `new` → `profiled` → `shortlisted` / `dismissed` |
| `outreachplan`, `outreachdraft` | Draft: `pending_review` → `approved` → `sent` → `replied` / `skipped` |
| `llmusage` | One row per Claude call: agent, model, tokens, cost |
| `runlog` | One row per run with statistics and any error |

New columns are added to existing tables automatically on start-up, so upgrading never loses data.

### Design rules

- **Every Claude call goes through `llm.parse()`**, which checks the budget, validates structured output
  and records cost even when a response is truncated.
- **No prompt hard-codes the user.** Names, cities, visas and target roles come from `persona.py`, which
  reads the profile and preferences.
- **Free work first.** Rules, regexes and HTTP requests filter and enrich before any tokens are spent.
- **Grounding over trust.** Anything written about the user is traced to profile facts and checked by rules
  and a second model. Repairs fall back to the user's own words.
- **Human in the loop.** Code never sends, posts or submits.

---

## Development and testing

```bash
python -m pytest -q
```

- Tests run against a **fictional persona** (`tests/fixtures/profile.yaml`, "Alex Sample") in a temporary
  config folder set up by `tests/conftest.py`. They never read your real config, so they behave the same
  on any machine.
- Claude calls are replaced with fakes; the suite costs nothing and needs no API key.
- Form filling is tested against local mock copies of Greenhouse, Lever and Ashby forms
  (`tests/fixtures/mock_forms.py`) with fake data. Each mock records whether Submit was clicked, and the
  tests assert it never is. These tests are skipped if no browser can start.
- The new-user journey (empty config → resume import → search settings) is tested through the real
  dashboard with Streamlit's `AppTest`.

**Extending:**

- *A new job source:* add a fetcher returning `RawPosting` objects in `scout/sources/` and call it from
  `scout/pipeline.fetch_all`.
- *A new job board for form filling:* add a reader in `apply/schema.py` returning `FormField`s, and any
  special widget handling in `apply/filler.py`.
- *A new agent:* route its Claude calls through `llm.parse(agent="yourname.step", …)` and add a
  `per_agent_daily` share if it runs on a schedule.

The dashboard reloads changed `jobsearch` modules on page refresh, so code changes show up without
restarting it.

---

## Troubleshooting

| Symptom | Fix |
|-|-|
| `400 … must include the anthropic-workspace-id header` | Your key is organisation-level. Add `ANTHROPIC_WORKSPACE_ID` (Job search → API keys), or use a key created inside a workspace. |
| `401 authentication_error` | The API key is wrong or revoked. Replace it in Job search → API keys. |
| A run stops with "daily cap reached" / "share reached" | Working as intended. Raise the cap in Job search → Budget, or click a button for that item (manual actions ignore agent shares). |
| "No Chromium-based browser could be started" | Install Edge or Chrome, or run `python -m playwright install chromium` (Linux: `--with-deps`). |
| Resume import finds almost no text | The PDF is probably a scanned image. Upload the DOCX or paste the text. |
| No jobs shortlisted | Check the **Jobs** tab: widen `title_include`, add cities, add Adzuna/Jooble keys, or lower `shortlist_threshold`. |
| A pre-filled form has an empty field | Job-board pages change. Fill it in the window before submitting; *Preview fill* lists anything it couldn't fill. |
| Application says **guided** | The job came from an aggregator or an unsupported site. Use the copy-paste kit and the application link. |
| Scheduled runs don't happen | `python -m jobsearch schedule show`, then check `data/logs/`. On Windows, tasks run only while you're logged in. |

---

## Limitations

- **Australia only.** Job sources, city matching, work-rights handling and startup news are Australian.
- **Only three job boards support pre-filling.** Most aggregator (Adzuna, Jooble) listings lead to other
  sites through bot-protected redirects, so those applications are guided. Many large employers (Workday,
  SuccessFactors) need an account you create yourself.
- **Job-board pages change.** Pre-filling can break on a changed page; *Preview fill* shows what it
  couldn't fill.
- **Website and email discovery are best-effort.** Some company sites block automated reads, and guessed
  websites can be wrong; startup profiles flag websites that don't match the news.
- **LinkedIn limits:** the 200-character connection-note default suits free accounts; Premium allows 300.
- **macOS scheduling is untested.** The launchd agents are generated and unit-tested, but not yet run on a
  real Mac. Windows and Linux (Ubuntu) are tested end to end.
- **Costs vary** with job-ad length and how many jobs match; the table above shows measured averages.
