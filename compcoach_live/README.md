# CompCoach Live

Mobile-first AFM staff coordination for external fencing competitions. This is
separate from the internal FencingAPP tournament manager.

CompCoach answers two operational questions:

1. Is the athlete still in the competition?
2. Does the athlete need a coach right now?

It intentionally stores no bout scores, opponents, tableau, seed, or ranking.
Only the pool W/L summary and DE outcome needed for live coordination are kept.

## Included in v0.6.0

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
- SQLite remains the active database. The persistence boundary is ready for a
  future Supabase/PostgreSQL adapter, but this release is **not connected to
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
- Main/Side coach assignment by athlete, plus DE assignment by pod.
- Live calls: `In the Hole`, `On Deck`, and `Now`, with automatic timestamp.
- A personal `My Group` dashboard for both Coach and Admin links, containing
  every athlete assigned as Main or Side coach plus athletes the person is
  currently covering. The coach's event is highlighted and temporary
  cross-event coverage remains visible.
- A mobile-first `Situation` page with three compact views: `Now`,
  `Assignments`, and `Pool Results`. `Assignments` retains the read-only Team
  Plan grouped by event, phase, and coach, so everyone can see the complete
  staff plan without opening the Admin tools.
- Meet-wide manual coach availability. A coach can tap `I'm available to help`
  after finishing their current duties, see how long the status has been
  active, and remove it at any time. The app shows an extra confirmation when
  its data still indicates unfinished work; availability is never inferred
  automatically and does not mean that the coach is online.
- The `Now` situation view shows every active Direct Elimination athlete by
  event and sector/pod, including Main and Side assignments, current coverage,
  live-call state, assignment exceptions, unassigned athletes, and unknown
  sectors. `Won` athletes remain in the sector load; `Lost` athletes move to
  Out and are excluded from that active load.
- Admin and Coordinator can atomically `Send an available coach to a DE
  sector` as Side support. The operation rechecks the coach's availability,
  requires confirmation before replacing existing Side support, preserves
  athlete-specific assignment exceptions, and clears the coach's available
  status in the same transaction.
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
  require a second confirmation tap.
- Independent red/green start indicators for Pools and Direct Elimination in
  every event. All coaches see them; Admin and Coordinator can change them.
- One-tap help requests showing athlete, strip/pod, requester, elapsed time,
  acknowledgement (`I'm coming`), and resolution to every connected coach.
  Alerts are global across the current competition day and always carry their
  event tag.
- One-tap coverage, atomic first-coach-wins claim, release, and coordinator
  coverage on another coach's behalf.
- Clearer Live-board filters: `Needs Coach`, `Covered Now`, `No Current Call`,
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
- Mobile Live Board and one WhatsApp schedule covering all events, with blue
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
unzip CompCoach_Live_v0.6.0.zip
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

After updating an existing installation—or after rebuilding the Codespace—make
sure the system OCR library is present, then reinstall the Python requirements
before restarting Streamlit. Version 0.6.0 uses the current RapidOCR package
and works with the Python 3.14 runtime used by newer Codespaces:

```bash
sudo apt-get update
sudo apt-get install -y libgl1
python -m pip install -r compcoach_live/requirements.txt
```

The first screen creates a competition day. Its first event is optional, and up
to four events can be added from Admin Setup. The Admin Share screen then provides:

- Coach Live Board link
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

See `.streamlit/secrets.example.toml`.

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

The live board and lifecycle check refresh every five seconds while the
Streamlit browser session is active. Help requests already appear as prominent
in-app alerts with requester, athlete, location, acknowledgement, and elapsed
time.

This version does **not** yet provide operating-system push notifications. A
phone that is locked, suspended, offline, or has the browser session stopped
cannot be relied on to play a sound or show a notification banner. Real
lock-screen notifications require a stable HTTPS production deployment plus a
PWA/Web Push implementation and per-device permission. Until that is built and
field-tested, WhatsApp and the open live board remain the operational alert
channels.

The database file **must** live on persistent storage before a real
competition. Platforms with ephemeral disks can lose the file after a restart
or redeploy. A public deployment must also set both `COMPCOACH_ADMIN_PIN` and
`COMPCOACH_PUBLIC_URL`.

SQLite is appropriate only for one application instance. Do not run multiple
replicas against separate local files. For a multi-instance or ephemeral
deployment, a real Supabase/PostgreSQL adapter and migration must be implemented
and tested before field use. The persistence layer is isolated in `storage.py`
to make that replacement possible without redesigning the parser or live UI;
there is no active Supabase connection in v0.6.0.

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
