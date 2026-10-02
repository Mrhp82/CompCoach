# CompCoach Live

Mobile-first AFM staff coordination for external fencing competitions. This is
separate from the internal FencingAPP tournament manager.

CompCoach answers two operational questions:

1. Is the athlete still in the competition?
2. Does the athlete need a coach right now?

It stores no bout scores, imported DE bracket, seed, or ranking. It keeps the
pool W/L summary, DE outcomes and byes, an optional starting tableau size,
and optional manually linked AFM-versus-AFM opponents.

## Included in v0.10.7

### New in v0.10.7

- **🚨 Need help now** stays directly visible beside DE result controls on every active athlete card in **My Group** and **Team situation**, including athletes assigned to another coach. It is also immediately available on your current-bout bar beside **Won / Lost**, with no confirmation dialog. Requests keep the named athlete and actual strip; a request sent from an obsolete screen cannot overwrite a newer update.
- **Current bout details** now sits directly beneath the current-bout bar. Use it for the full call/strip and coverage controls; finishing the bout and requesting help remain visible above it. The same current athlete is not repeated in the ordinary active list.
- The autonomous help lesson first asks you to arrive with the athlete you took over on **J2**. A different, uncovered athlete then appears **Now on K4**. Request help on that athlete's card while continuing your current bout; a virtual colleague responds. The guide follows your saved arrival, help request and result rather than asking for help on the athlete you are already covering. Existing personal practice links and progress are retained.
- DE progress separates byes from fenced wins: **1 bye · 2 DE wins · Waiting for DE bout 3**. A fresh call or physical coverage changes the waiting text to **DE bout 3**. Admin may optionally set an event's starting DE tableau size; a known start of 256 with three rounds passed gives **T32**. Without this setting, the app shows recorded progress without guessing an official tableau round.
- **Bye** and **⚠️ Missed coaching** remain in **More actions**; urgent help is available without opening that section. Pool help remains reserved for real emergencies.

### New in v0.10.6

- **⚠️ Missed coaching** records an athlete's bout without coaching with one tap, separately from **WIN / LOST**, a Pool W/L summary, or an Out state. It does not enter or change a competitive result. Recording an incident does not require writing an explanation first.
- The collapsed **⚠️ Missed coaching · N athletes** section in **Team situation** retains these reports. **Athlete to report → Record missed coaching** supports retrospective reports for athletes already Out or with completed Pools, with **Notes (optional)**. Admin can also open a closed day through **Home → History → Open read-only archive** and review its saved reports.
- Each report saves the staff state **at reporting time**, across every event in that competition day: recorded physical coverage with athlete and strip, reserved takeovers, explicitly declared availability, and unconfirmed availability. This snapshot describes the app's records; it does not establish who was truly free during the bout or why coaching was missed.
- Incorrect reports use **Mark report as incorrect**, with an optional correction note and an audit record. Correcting a report does not restore an athlete, undo a result, change assignments, or use the generic athlete-action Undo.
- Result controls emphasize **WINS / WIN** in green and **LOSSES / LOST** in red. Pool choices **0–6** use equally sized buttons in four columns and two rows, keeping wins and losses easy to tap on a phone.
- DE cards keep the athlete's name and actual strip beside their actions. The current call is a note; one compact row shows only the next useful choices: **Not called → In the hole / On deck / Now**, **In the hole → On deck / Now**, **On deck → Now**. **Modify call** exposes every status and the actual-strip editor for corrections. Known strips stay out of the default input area; an unknown-strip input remains directly accessible, and calls still save without a strip.
- **I’m with [athlete]** is a primary action and disappears after physical coverage. An Admin who is also an active coach has the same direct self-coverage action; choosing another coach stays in the collapsed **Coach coverage** section. The compact **WIN / LOST** row precedes **More actions**, which holds optional Bye and missed-coaching controls. Help moved out of that section in v0.10.7.
- Practice recognizes a saved athlete call from either **My Group** or **Team situation**, including **Report athlete location → Publish update**, without repeating it in the other view. The call lesson accepts a confirmed strip too; leave it empty when unknown. Both views also share the same saved data during a real competition. Lessons specifically about visiting a screen still require that visit.
- The missed-coaching report is available during practice as an optional manual exercise. It is not a required step in the autonomous course.

### New in v0.10.5

- The practice guide now uses native HTML rendering, so it displays as a compact fixed bar instead of showing HTML text. Its next action remains visible while scrolling; the full explanation stays in **Need a hint?**.
- The shared operational page is labelled **Team situation**. Existing links and saved navigation still work; the CompCoach Live application name is unchanged.
- Ordinary Pool groups and DE pod assignments are implicit: coaches see them in **My Group** without accepting each athlete. Automatic assignment notices from v0.10.4 are retired from the interface; stored assignments, results and historical notice records are retained.
- Admin and Coordinator can use **Request coverage for a bout → Request coverage** for an unexpected bout needing help, selecting one or more currently available coaches. Sending an offer does not reserve anyone or change the Pool/DE pod plan.
- The first coach to tap **I'll cover this bout** takes temporary responsibility for that bout. The offer closes for every other recipient; the accepting coach leaves Available, while the other coaches remain free. The athlete appears in that coach's **My Group** without changing the planned coaches.
- Acceptance and arrival are separate. **I’m with [athlete]** records physical presence and starts the busy timer, including for an accepted Pool emergency. Finishing the Pool result clears its call, help, takeover and coverage, and releases the coach when no other live duty remains.
- Real coverage offers expire after **15 minutes**. Admin/Coordinator can cancel an unanswered offer and send a new one if circumstances change. In autonomous practice, an unanswered expired or cancelled lesson offer is renewed as a fresh request; accepted or already covered bouts are never reclaimed automatically.

### New in v0.10.4

Historical release notes: the automatic assignment-acceptance notices described
below were replaced by explicit single-bout coverage requests in v0.10.5.

- **My Group** puts the athlete you are physically covering directly beneath its heading, with actual strip, busy timer and immediate **Won / Lost** commands for DE. Finishing the bout releases your coverage; detailed controls remain in **Current bout details**.
- The personal DE list brings accepted takeovers and fresh **Now → On deck → In the hole** calls ahead of the waiting wheel. Old calls have a separate **Calls to verify** section; athletes already covered appear below current work. Uncalled winners still rotate to the end after each result.
- A new or changed assignment has a visible **Accept assignment** notice showing the athlete, event, pod or pool, and current strip/call. Acceptance confirms responsibility; it does not claim physical arrival or start the busy timer. Admin/Coordinator can inspect assignment confirmations.
- **I’m with [athlete]** disappears after physical coverage is recorded. Calls, actual-strip edits, help, release and result controls remain available. **I’ll take over** still reserves responsibility until physical arrival is confirmed.
- Pool help guidance reserves **Need help** for real emergencies, such as several missed bouts or an athlete left without coaching. Ordinary pool coordination should respect that the other coaches are also busy.
- Practice activation needs only a **7- or 14-day** duration. Coaches open the common link, type **Your name**, and tap **Start my practice** to begin a separate course. Equal display names never share progress; the personal practice URL resumes that course after reloads.
- During an active personal practice, a compact guide remains fixed below the app header while scrolling. It shows the current step and next action; **Need a hint?** retains the full instructions, scenario, feedback and expiry. Practice explicitly teaches confirming a new assignment before heading to the bout.

### New in v0.10.3

- Real competitions and practice use one lightweight five-second change check. Idle checks do not redraw athlete cards, navigation or input controls. Calls, help requests, availability, results and competition-day changes still refresh all open boards.
- Repeated reads are reused only within the current render. Every new interaction and automatic check reads current storage; writes retain the existing concurrency guards.
- Autonomous practice runs the scenario engine for coach actions, a new lesson/view, due simulated events or access termination. Observation bookkeeping alone does not restart the page; elapsed-time labels update once per minute when needed.

### New in v0.10.2

- Community Cloud deployment preparation now writes only package names to `packages.txt` and repairs comments left by earlier releases while preserving existing dependencies.

### New in v0.10.1

- Add a new coach directly in **New competition → Coaches present** or **Practice → Coaches taking part**: type the name in the dropdown and select **Add**, then submit the form. Coordinators also support direct entry.
- New competition offers the saved general coach directory and saves new names for future competitions. Existing names keep their directory spelling and are deduplicated across case and extra spaces.
- New practice names stay in the exercise, allowing fictional demo coaches without adding them to the real staff directory.

### New in v0.10.0

- Admin can activate **autonomous practice** for **7 or 14 days** from **Setup → Training** or **Home → Practice** and share one dedicated coach link. Admin does not need to supervise, advance scenarios, or remain connected.
- Each coach receives a complete personal course. Returning to its personal practice URL resumes that course; the common entry link starts a separate course from the beginning. Other coaches' actions do not skip their lessons.
- The app plays the coordinator and virtual colleagues. It prepares pool and DE assignments, starts phases, requests help, calls athletes, and reacts to the learner's coverage and results. Delayed coverage can arrive or remain unavailable; sudden calls vary between Now, On deck and In the hole. A free learner can receive a new assignment across events.
- The normal My Group and merged Live commands are used throughout. Tasks and hints guide pool results, optional absence, availability, calls with unknown strips, physical coverage, takeovers, emergency help, the DE result wheel, byes and same-club bouts through the end of the fictional day.
- Completed coaches can tap **Practice again** during the activation period. Replays change coverage and call scenarios. Progress and scheduled simulation events survive reloads and application restarts.
- Practice has separate competitions, event IDs, tokens and fictional athletes. It is excluded from ordinary competition History and season assignment history. Virtual staff do not enter the real coach directory. Expired or ended practice also rejects writes sent from stale screens.

See [TRAINING.md](TRAINING.md) for activation and operating instructions. The practice period controls access; the application must still be hosted on a running service. This feature does not keep a Codespace awake.

### New in v0.9.5

- **Live** now combines the athlete operations and former Situation overview. A single athlete list retains the existing coverage filters, pool cards and DE wheel; **My Group** remains the personal dashboard.
- Live has compact, collapsed sections for **Coaches**, **DE sector load**, **All assignments** and **Pool results**. Availability management and cross-event deployment remain available to Admin/Coordinator.
- The quick call/strip form and shared overview refresh together with Live every five seconds. Global phase lights, available coaches, busy-coach takeovers and help alerts remain visible across pages.
- Open sessions previously on Situation move to Live; a previously selected assignment or pool-results view opens its matching section.

### New in v0.9.4

- Now, On deck and In the hole can be saved before the actual bout strip is known, both on athlete cards and in the coordinator's quick update. The call keeps its reporter and timestamp; the imported pod reference stays separate.
- The actual-strip field is emphasized and marked optional for call updates. Unknown-strip calls have a clear save confirmation and a persistent **Actual strip to confirm** notice; enter the strip later and tap the call again to update it.

### New in v0.9.3

- Accepting a takeover immediately removes the coach from the available list across all events. The personal dashboard shows the accepted takeover and prevents a conflicting availability declaration.
- Releasing the takeover or recording the result restores availability only when the coach has no other pending takeover or physical coverage. Pending responsibility still has no physical-coverage timer until the coach records being with the athlete.
- Existing takeovers also hide legacy available flags, and revision checks reject availability/deployment actions sent from an obsolete screen.

### New in v0.9.2

- Uncovered-athlete alerts put the athlete's name first, with larger bold call, actual strip, pod and event details. The assigned coach's busy status is a smaller note underneath; the one-tap takeover button remains beside the alert.

### New in v0.9.1

- Busy-coach warnings include **I’ll take over** for an available coach identity, including an Admin who is also a coach. No call or actual strip is required.
- This records temporary responsibility without changing pod assignments or inventing physical coverage. Everyone sees **Taken by [coach]**, and the athlete appears in that coach's My Group.
- **Release takeover** is available to the owner and Admin/Coordinator. Starting physical coverage or finishing the bout clears the promise. Actual busy status and its timer still start only when the coach records being with the athlete.
- Athlete and coach revision checks protect simultaneous taps and changes made on another phone; a coach physically busy elsewhere cannot take over from an obsolete screen.

### New in v0.9.0

- Direct Elimination is a continuous wheel: the most recent Won/Bye moves to the end; earlier winners rise as more outcomes are entered. All active athletes remain editable with round dividers and no readiness button.
- Imported DE strip (for example B1) is the pod calling reference, not the actual bout strip. Each card accepts an actual strip and one-tap Now / On deck / In the hole / Not called. Previous live strips are cleared after results.
- Coaches can mark themselves with an athlete even before a call; Admin/Coordinator can mark any active coach. Live coverage is exclusive across every event in the day and shows its own elapsed timer.
- A shared busy board shows other athletes assigned to an occupied coach, highlighting fresh calls and potential coverage gaps across events. Other planned coaches remain visible.
- Won/Lost releases the actual covering coach and makes that coach available; assignments remain in place. Individual and joint AFM corrections remain in Correct DE results.
- Footers and known website debris remain excluded on import and reversibly quarantined when previously saved.

### New in v0.8.0

- DE pods have coach groups without Main/Side ranks or a two-coach limit.
  Assign several coaches to one pod, or add one coach to one to four selected
  pods while retaining existing groups. Individual athlete exceptions remain
  possible. Pools retain their Main/Side roles.
- Every DE coach appears in My Group, event highlighting, workload, availability,
  team plans, pod summaries, and the shared WhatsApp message. Existing DE
  assignments migrate automatically; all coach intervals remain in the history.
- Staff can manually mark a same-event AFM-versus-AFM DE bout, optionally name
  its round, and record the winner. The winner stays active and the loser goes
  Out in one transaction. Correcting the result restores both athletes together;
  stale changes on another phone prevent an unsafe correction.
- BYEs are recorded separately from fenced wins, with one-tap entry and dedicated corrections
  for the latest BYE. Athlete cards, pod load, team plans and WhatsApp show
  rounds passed as BYEs plus wins: one BYE and two wins means three rounds.
  A pending AFM pairing must be removed or resolved before recording a BYE.
- OCR and pasted tables reject website names, URL fragments, navigation, and
  footer text before treating them as athletes. The OCR preview reports ignored
  debris. Existing imported records are retained and can be marked withdrawn.

For a mobile check: assign three coaches to one DE pod, add one of them to two
more pods, then open each coach link. Pair two AFM athletes, record a winner,
check the result from a second phone, and undo it. Also check a Pool assignment
still displays its Main and Side correctly.


### New in v0.7.3

- All active staff pages highlight explicitly available coaches in a compact
  green panel with elapsed time. The panel refreshes every five seconds and
  covers the whole competition day, across event assignments. Existing help
  alerts, personal availability controls, and coordinator deployment tools
  remain available.
- Coach pool cards can mark an athlete absent after confirmation. The athlete
  leaves operational lists while assignments and results are retained. A
  collapsed absent-athlete list in My Group and Live supports reversal through
  the same guarded attendance API used by Admin.

### New in v0.7.2

- The Add Coach form explicitly separates present for this day, competition
  only, and general directory only. The default makes the coach immediately
  available in the day roster and assignment lists. Adding an existing name
  reuses its identity and retains existing competition roles.

### New in v0.7.1

- Adding a coach selects the new entry on the next render, avoiding a
  Streamlit state error after the coach was already saved. The coach is
  created once and can immediately be marked present for the day.

### New in v0.7.0

- A neutral Admin home separates active competition days, scheduled days, and
  archives. Finishing a day without preparing another stops live activity;
  closing the whole competition archives its days and retains the season
  history. New competitions no longer require deleting earlier data.
- A real PostgreSQL backend supports Supabase while retaining the same
  operational storage API and transaction guards. Set
  `COMPCOACH_DATABASE_URL` to opt in. SQLite remains available for local tests;
  the cloud connection never migrates the SQLite data automatically. The cached
  backend uses a bounded connection pool to reuse secure database connections
  across mobile refreshes without creating unbounded connections.
- Competition logos and strip maps can use a private Supabase Storage bucket.
  Cloud data and assets survive a Streamlit host restart. Set
  `COMPCOACH_REQUIRE_CLOUD=true` in production so an incomplete cloud
  configuration fails clearly instead of falling back to a local file.
- `migrate_to_supabase.py` makes an explicit, one-shot SQLite-to-PostgreSQL copy
  into an empty destination. It preserves IDs, shared-link tokens, days,
  results, and assignment history; validates local images before migration;
  refuses to overwrite existing destination data or objects; and imports
  database rows in a single transaction. Its default is a local dry run.
- The project can stay inside `compcoach_live` when hosted. Repository-root
  deployment templates and a simple `launch.sh` are included. Read
  [DEPLOYMENT.md](DEPLOYMENT.md) for the Italian step-by-step guide.

The backend and deployment files are supplied in this release. They are not a
claim that your Supabase project has already been connected or that an online
deployment has been created. Field use requires the configured project and
the multi-phone checks in the deployment guide.

### New in v0.6.0

- Strip and sector labels use natural ordering in assignment lists: `B1`,
  `B2`, ... `B9`, `B10`, `B20`. The same rule works with other letter and
  number combinations, while blank/TBD locations stay at the end.
- Coach pickers show coaches not yet used during the competition day first.
  Coaches who already have an athlete or active sector assignment move to the
  bottom and are marked `already assigned`, but remain selectable. Assignment
  to one event is never a barrier to urgent support in another event.
- The imported Pools `Time` column creates ordered start-time waves. Admin can
  prepare assignments for `All start times` or filter one wave at a time. The
  first timed wave is current by default; activating another wave updates the
  operational coach views. Untimed legacy rows remain visible.
- Admin can mark an imported athlete `Absent` or `Withdrawn` and restore them
  after a mistake. These states are separate from competitive elimination:
  the athlete leaves current assignment worklists, Live, Situation, WhatsApp,
  workload, help, and phase counts without losing the saved plan or history.
  Reimporting the athlete does not silently reactivate them.
- Saving a Pools W/L result removes that athlete from the working cards in
  `My Group` and places the entry in a collapsed `Completed pool results`
  section. The result remains visible and editable. Direct Elimination still
  stores only `Won` or `Lost`, never the score.
- The SQLite schema now has a season-level competition parent with location,
  start/end dates, logo and strip-map references; a durable coach directory,
  competition roles, day presence, optional home-event preference, and
  interval assignment history. Admin `Settings` exposes four compact mobile
  sections: `Day`, `Competition`, `Coaches`, and `Schedule`. Uploaded images
  are validated and the full-resolution strip map remains available to every
  coach from the shared board.
- Future competition days can be prepared with zero to four events without
  appearing on the active Coach or Coordinator board. Admin may import and
  assign those future events in advance. A scheduled day can be activated only
  after the current active day is finished, so changing the calendar cannot
  silently replace live competition data.
- The coach setup separates the season directory, competition roles, and
  today's presence. A coach may have multiple roles and an optional home event;
  the home event is only a visual priority and never blocks emergency work in
  another event. Coach renames retain their stable identity and update current
  operational assignments without rewriting historical audit bylines.
- In v0.6.0 SQLite remained the active database. The persistence boundary was ready for a
  future Supabase/PostgreSQL adapter, but that release was **not connected to
  Supabase** and setting Supabase credentials alone will not migrate or sync
  any data.

### Existing live-coordination workflow

- A clear `Who are you?` screen at entry. Staff choose their name with one tap
  before opening the board; there is still no staff account or password to
  manage. The shared role token remains the access credential.
- One competition-day link can contain from zero to four simultaneous events.
  Athletes, imports, assignments, phase state, results, calls, and source URLs
  remain isolated inside their event, while staff can move between events from
  the same board. A day can be created before its event schedule is known, and
  the final empty event can be removed.
- Pools and Direct Elimination import from Markdown, tab-separated, simple
  HTML tables, or a PNG/JPEG screenshot.
- Header-based parsing for `Name`, `Strip #`, `Time`, and `Pool #`.
- Screenshot recognition runs locally on the server. Its result is always
  shown in an editable preview before import; uncertain or missing cells are
  never silently invented. The operator must also choose the destination event.
  Replacing the file, phase, or edited rows resets its review confirmation.
- `No matching records found` is a safe no-op by default. Ordinary or partial
  imports never eliminate missing athletes; only the separately confirmed
  complete DE-list workflow—including a strongly confirmed final empty list—
  can infer non-advancers.
- Admin, Coordinator, and Coach links use random tokens and require no staff
  login. A shared link covers the whole competition day and all its events.
- Main/Side Pool assignments; equal DE coach groups by athlete or pod.
- Live calls: `In the Hole`, `On Deck`, and `Now`, with automatic timestamp.
- A personal `My Group` dashboard for both Coach and Admin links, containing
  every athlete assigned as Main, Side, or equal DE coach plus athletes the
  person is currently covering. The coach's event is highlighted and temporary
  cross-event coverage remains visible.
- **Team situation** combines live athlete operations with collapsed sections
  for Coaches, DE sector load, All assignments and Pool results. The read-only
  Team Plan is grouped by event, phase and coach, so everyone can see the staff
  plan without opening Admin tools.
- Meet-wide manual coach availability. A coach can tap `I'm available to help`
  after finishing their current duties, see how long the status has been
  active, and remove it at any time. The app shows an extra confirmation when
  its data still indicates unfinished work. Finishing or releasing a bout can
  restore availability when no other live responsibility remains; availability
  does not mean that the coach is online.
- **Team situation** shows every active Direct Elimination athlete by
  event and sector/pod, including all equal DE coaches, current coverage,
  live-call state, assignment exceptions, unassigned athletes, and unknown
  sectors. `Won` athletes rotate to the end of the live queue; `Lost` athletes move to
  Out and are excluded from that active load.
- Admin and Coordinator can deploy available coaches to DE sectors as equal
  support. Deployment rechecks availability, preserves athlete-specific
  assignment exceptions and clears the deployed coach's available status.
- Planned Pool and DE assignments need no acceptance. For urgent temporary
  cover, **Request coverage** offers one bout to one or more available coaches.
  **I'll cover this bout** reserves the first accepting coach; physical arrival
  is recorded separately with **I’m with [athlete]**.
- Availability is also cleared automatically when a coach is deployed through
  an assignment, claims live coverage, is assigned as current coverage, or
  becomes involved in an active help request. This prevents a coach who has
  just accepted work from remaining green on another phone.
- `Pool Results` lists every recorded pool W/L summary across the whole day,
  including athletes who later advanced to DE or moved to Out. Missing results
  are shown as awaiting only while the athlete is still active in Pools; the
  summary never infers advancement or placement.
- Scoreless result summaries: pool wins/losses use two mobile-friendly 0–6 tap
  selectors; DE keeps only Won/Lost and a running count of wins. DE outcomes
  save immediately; **Correct DE results** handles mistakes separately.
- Independent red/green start indicators for Pools and Direct Elimination in
  every event. All coaches see them; Admin and Coordinator can change them.
- One-tap help requests showing athlete, strip/pod, requester, elapsed time,
  acknowledgement (`I'm coming`), and resolution to every connected coach.
  Alerts are global across the current competition day and always carry their
  event tag.
- Pool help is reserved for genuine emergencies. An accepted emergency coach
  can confirm physical arrival; completing the Pool result clears the
  temporary assistance and releases eligible coaches.
- One-tap coverage, atomic first-coach-wins claim, release, and coordinator
  coverage on another coach's behalf.
- **Team situation** filters: `Needs Coach`, `Covered Now`, `No Current Call`,
  and `Out`. These labels distinguish an athlete who has no current parent or
  coordinator call from one who actively needs coverage.
- Updating a call preserves existing coverage unless the operator explicitly
  changes it; stale phone screens cannot overwrite newer live state.
- `Clear call` returns incorrect or obsolete information to Waiting.
- Freshness warnings at 15/25/35 minutes for Now/On Deck/In the Hole.
- `Won` keeps a DE athlete active; `Lost` moves them to Out. No scores are kept.
- Pools athletes who do not advance can be moved to Out without recording a
  fictitious result. Every Out action is reversible.
- Athlete-specific coach exceptions take precedence over pod defaults on later
  imports.
- Activity history and guarded Undo.
- Mobile **Team situation** board and one WhatsApp schedule covering all events, with blue
  Main and orange Side markers and distinct coach-name emphasis.
- Safe-area-aware top spacing so the competition title remains visible on
  desktop and mobile browsers.
- Full Coach and Coordinator share links detected automatically from the
  browser URL, with `COMPCOACH_PUBLIC_URL` available as an explicit override.
- Explicit end-of-day flow: finish the current day as a read-only archive and
  return every Coach/Coordinator board to an operational zero, or finish it and
  prepare the next day with the same staff and timezone. The next day may start
  with zero to four events. Operational data starts clean and the previous
  day's data remains preserved as an Admin archive.
- Coach and Coordinator pages check the day lifecycle every five seconds. An
  old shared link automatically follows the prepared-day chain to the newest
  day, preserving the role and a still-authorized identity. If no next day
  exists, it shows a closed/waiting screen instead of the old live data.
- Admin can deliberately open a past day as a read-only archive without being
  redirected to its successor.
- Pool cards no longer ask a coach to decide whether an athlete advanced. A
  complete Direct Elimination import can infer the missing pool athletes only
  after one explicit whole-list confirmation; the app lists every affected
  name first, records the action, and keeps it reversible.
- OCR server failures now show a useful technical detail and a specific
  Codespaces recovery command instead of blaming a valid screenshot.
- Saved Fencing Time Live URL as a source/reference link only. Data import from
  that URL remains manual; automatic extraction is disabled until an
  authorized data interface is available.

## Unzip and run in a Codespace

From the directory containing the release archive, use a new/empty destination
so an older working copy is not overwritten:

```bash
unzip -o CompCoach_Live_v0.10.7.zip
cd compcoach_live
sudo apt-get update
sudo apt-get install -y libgl1
python -m pip install -r requirements.txt
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

Streamlit prints the port-8501 URL in the terminal. In GitHub Codespaces, make
port `8501` public before sharing a Coach or Coordinator link. For a repository
that is already checked out and has uncommitted work, update it through Git
instead of extracting a release ZIP over the same folder.

## Run from the repository parent directory

```bash
sudo apt-get update
sudo apt-get install -y libgl1
python -m pip install -r compcoach_live/requirements.txt
python -m streamlit run compcoach_live/app.py --server.address 0.0.0.0 --server.port 8501
```

Alternatively, use `bash compcoach_live/launch.sh` from the repository root,
or `bash launch.sh` from the application folder. The launcher retains the
working directory so Streamlit finds the intended `.streamlit` configuration.

After updating an existing installation—or after rebuilding the Codespace—make
sure the system OCR library is present, then reinstall the Python requirements
before restarting Streamlit. This release uses the current RapidOCR package
and works with the Python 3.14 runtime used by newer Codespaces:

```bash
sudo apt-get update
sudo apt-get install -y libgl1
python -m pip install -r compcoach_live/requirements.txt
```

The first screen creates a competition day. Its first event is optional, and up
to four events can be added from Admin Setup. The Admin Share screen then provides:

- Coach Team situation link
- Coordinator link

Each shared link is a credential for its day and automatically moves forward
only through days created with `Prepare next day`. It can be regenerated from
Admin Settings if it is forwarded outside the staff. Choosing a name at the
`Who are you?` screen identifies actions and personalizes the dashboard; it is
not a replacement for the link token.

Save the private Admin URL immediately after creating the first competition
day.
Without an Admin PIN, the landing page intentionally does not reveal existing
days or their Admin links.

## Configuration

Environment variables or Streamlit secrets:

| Setting | Purpose |
| --- | --- |
| `COMPCOACH_ADMIN_PIN` | Protects event creation and the local Admin event list |
| `COMPCOACH_PUBLIC_URL` | Optional base-URL override used to generate complete share links |
| `COMPCOACH_DB_PATH` | SQLite path; point this at persistent storage in production |
| `COMPCOACH_DATABASE_URL` | Opts into the PostgreSQL/Supabase backend; use the Session pooler connection string |
| `COMPCOACH_REQUIRE_CLOUD` | Refuses missing database/asset cloud configuration when `true` |
| `SUPABASE_URL` | Project URL for private cloud asset storage |
| `SUPABASE_SECRET_KEY` or `SUPABASE_SERVICE_ROLE_KEY` | Server-only private key for the asset bucket |
| `COMPCOACH_STORAGE_BUCKET` | Private asset bucket name; default `compcoach-assets` |

See `.streamlit/secrets.example.toml` for local tests and
`deployment/repository_root/.streamlit/secrets.example.toml` for cloud setup.
Actual Secrets must never be committed. Environment variables take precedence.

## Storage and deployment

SQLite runs in WAL mode and is shared safely by all phone sessions connected to
one Streamlit server. Claims use an immediate transaction, so only the first
coach can cover an athlete. Every competition day owns zero to four child
events; event data is isolated, while day-wide help alerts are aggregated for
all staff.

The database also stores a parent competition and its scheduled/active/closed
days, competition date range and assets, coach membership and daily presence,
and coach-assignment intervals. These controls are available in Admin
`Setup → Settings`. The established `Prepare next day` shortcut remains
available, while `Schedule` can prepare multiple future days without adding
them to today's operational board.

## Refresh and phone notifications

One lightweight check runs every five seconds while the Streamlit browser
session is active, for both real competitions and practice. It refreshes the
board only when operational data or visible practice instructions change;
unchanged checks leave cards and draft inputs alone. Elapsed-time labels update
once per minute while calls, coverage, availability or phase timers are visible.
Help requests appear as prominent in-app alerts with requester, athlete,
location, acknowledgement, and elapsed time. A transient polling connection
failure preserves the current board and retries at the next check.

This version does **not** yet provide operating-system push notifications. A
phone that is locked, suspended, offline, or has the browser session stopped
cannot be relied on to play a sound or show a notification banner. Real
lock-screen notifications require a stable HTTPS production deployment plus a
PWA/Web Push implementation and per-device permission. Until that is built and
field-tested, WhatsApp and the open live board remain the operational alert
channels.

When running on SQLite, the database file **must** live on persistent storage
before a real competition. Ephemeral hosts can lose local files after a restart
or redeploy; SQLite is suitable only for one application instance.

For cloud hosting, configure the PostgreSQL backend and private Supabase bucket
using [DEPLOYMENT.md](DEPLOYMENT.md). Data and images remain in Supabase while
Streamlit runs on a separate hosting service. A public deployment must also set
`COMPCOACH_ADMIN_PIN` and `COMPCOACH_PUBLIC_URL`. Backend failures produce an
operational error; they do not silently redirect writes to SQLite.

The database URL selects a backend; it does not synchronize databases. Use the
explicit migration once if existing local data is needed, then direct every
coach to the same cloud deployment. Old tokens can be preserved, but the old
Codespaces URL is not redirected to the new domain automatically.

## Import behavior

Every import is applied only to the event selected by the operator. Reimporting
merges by stable normalized athlete identity within that event. It can update
phase, pool, pod, strip, and time while retaining live coverage and state.
Moving from Pools to DE closes stale pool calls and clears pool-specific
planned coaches. The UI asks for explicit confirmation before moving an
existing DE athlete back to Pools.

For Pools, non-empty imported `Time` values are normalized into ordered waves.
Assignment setup may show every wave at once; live operational views show only
the current timed wave plus athletes that have no start time. Changing the
current wave does not remove or overwrite athletes in the other waves.

`Absent` and `Withdrawn` are reversible participation states. They are not
synonyms for `Out`, and the absence of a name from a later import never changes
participation automatically.

For screenshots, upload PNG or JPEG, review and correct the locally recognized
rows, then apply the same import validation used for pasted tables. Cropped or
partial screenshots are accepted; only visible, recognized rows are proposed.

The absence of an athlete in an ordinary or partial later list never means
elimination. Only an explicit `Lost` action or a separately confirmed complete
DE list can change an athlete to `Eliminated`. A final empty DE list requires
the stronger confirmation that no listed Pools athlete advanced; an empty list
is otherwise always a no-op.

## Tests

From the repository parent directory:

```bash
pytest -q compcoach_live/tests
```

Or, after `cd compcoach_live`:

```bash
pytest -q tests
```

Parser and storage tests use temporary data only.
PostgreSQL migration and backend tests use an isolated disposable schema when
`COMPCOACH_TEST_POSTGRES_URL` is set. The local SQL checks cover row parity,
preserved IDs and operational workflows. PostgreSQL SQL compatibility has also
been exercised through PGlite's WASM engine and its psycopg wire server; that
emulator does not replace a native multi-session PostgreSQL test. The actual
hosted Supabase connection, concurrent phone sessions and Storage bucket still
need the acceptance checks in [DEPLOYMENT.md](DEPLOYMENT.md).
