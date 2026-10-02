-- CompCoach cloud schema. Keep this schema OUT of Supabase API exposed schemas.
-- Run with the private backend database role; no browser receives its password.
-- Integer flags/text timestamps intentionally match SQLite and preserve exports.
CREATE SCHEMA IF NOT EXISTS compcoach;
SET search_path TO compcoach, pg_catalog;

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'locked')),
    active_coaches_json TEXT NOT NULL DEFAULT '[]',
    coordinators_json TEXT NOT NULL DEFAULT '[]',
    admin_token TEXT NOT NULL UNIQUE,
    coordinator_token TEXT NOT NULL UNIQUE,
    coach_token TEXT NOT NULL UNIQUE,
    source_url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS competitions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'closed')),
    ended_at TEXT,
    ended_by TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL DEFAULT '',
    end_date TEXT NOT NULL DEFAULT '',
    timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
    logo_path TEXT NOT NULL DEFAULT '',
    strip_map_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meets (
    id TEXT PRIMARY KEY,
    competition_id TEXT REFERENCES competitions(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'locked')),
    active_coaches_json TEXT NOT NULL DEFAULT '[]',
    coordinators_json TEXT NOT NULL DEFAULT '[]',
    admin_token TEXT NOT NULL UNIQUE,
    coordinator_token TEXT NOT NULL UNIQUE,
    coach_token TEXT NOT NULL UNIQUE,
    ended_at TEXT,
    ended_by TEXT NOT NULL DEFAULT '',
    prepared_from_meet_id TEXT REFERENCES meets(id) ON DELETE SET NULL,
    competition_date TEXT NOT NULL DEFAULT '',
    day_status TEXT NOT NULL DEFAULT 'active'
        CHECK (day_status IN ('scheduled', 'active', 'closed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_sessions (
    meet_id TEXT PRIMARY KEY REFERENCES meets(id) ON DELETE CASCADE,
    source_meet_id TEXT REFERENCES meets(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'running',
    stage INTEGER NOT NULL DEFAULT 0,
    state_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meet_events (
    meet_id TEXT NOT NULL REFERENCES meets(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL UNIQUE REFERENCES events(id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL CHECK (sort_order BETWEEN 0 AND 3),
    PRIMARY KEY(meet_id, event_id),
    UNIQUE(meet_id, sort_order)
);

CREATE TABLE IF NOT EXISTS athletes (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    athlete_key TEXT NOT NULL,
    name TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'pools'
        CHECK (phase IN ('pools', 'de')),
    pool_no TEXT NOT NULL DEFAULT '',
    pod TEXT NOT NULL DEFAULT '',
    source_strip TEXT NOT NULL DEFAULT '',
    time_text TEXT NOT NULL DEFAULT '',
    main_coach TEXT NOT NULL DEFAULT '',
    side_coach TEXT NOT NULL DEFAULT '',
    de_coaches_json TEXT NOT NULL DEFAULT '[]',
    assignment_override INTEGER NOT NULL DEFAULT 0,
    active_state TEXT NOT NULL DEFAULT 'active'
        CHECK (active_state IN ('active', 'eliminated')),
    participation_status TEXT NOT NULL DEFAULT 'active'
        CHECK (participation_status IN ('active', 'absent', 'withdrawn')),
    call_status TEXT NOT NULL DEFAULT 'waiting'
        CHECK (call_status IN ('waiting', 'in_hole', 'on_deck', 'now')),
    live_location TEXT NOT NULL DEFAULT '',
    reported_at TEXT,
    reported_by TEXT NOT NULL DEFAULT '',
    covered_by TEXT NOT NULL DEFAULT '',
    covered_at TEXT,
    takeover_coach TEXT NOT NULL DEFAULT '',
    takeover_at TEXT,
    takeover_by TEXT NOT NULL DEFAULT '',
    pool_wins INTEGER,
    pool_losses INTEGER,
    pool_result_at TEXT,
    pool_result_by TEXT NOT NULL DEFAULT '',
    de_wins INTEGER NOT NULL DEFAULT 0,
    de_byes INTEGER NOT NULL DEFAULT 0,
    de_awaiting_next INTEGER NOT NULL DEFAULT 0 CHECK (de_awaiting_next IN (0, 1)),
    last_de_result TEXT NOT NULL DEFAULT '',
    last_de_result_at TEXT,
    last_de_result_by TEXT NOT NULL DEFAULT '',
    help_requested_by TEXT NOT NULL DEFAULT '',
    help_requested_at TEXT,
    help_location TEXT NOT NULL DEFAULT '',
    help_acknowledged_by TEXT NOT NULL DEFAULT '',
    help_acknowledged_at TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(event_id, athlete_key)
);

CREATE TABLE IF NOT EXISTS pod_assignments (
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    phase TEXT NOT NULL CHECK (phase IN ('pools', 'de')),
    pod TEXT NOT NULL,
    main_coach TEXT NOT NULL DEFAULT '',
    side_coach TEXT NOT NULL DEFAULT '',
    coaches_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(event_id, phase, pod)
);

CREATE TABLE IF NOT EXISTS phase_states (
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    phase TEXT NOT NULL CHECK (phase IN ('pools', 'de')),
    started INTEGER NOT NULL DEFAULT 0 CHECK (started IN (0, 1)),
    changed_at TEXT,
    changed_by TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(event_id, phase)
);

CREATE TABLE IF NOT EXISTS pool_waves (
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    wave_key TEXT NOT NULL,
    label TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 0
        CHECK (is_active IN (0, 1)),
    is_visible INTEGER NOT NULL DEFAULT 0
        CHECK (is_visible IN (0, 1)),
    activated_at TEXT,
    activated_by TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(event_id, wave_key)
);

CREATE TABLE IF NOT EXISTS coach_availability (
    meet_id TEXT NOT NULL REFERENCES meets(id) ON DELETE CASCADE,
    coach_name TEXT NOT NULL,
    is_available INTEGER NOT NULL DEFAULT 0
        CHECK (is_available IN (0, 1)),
    available_since TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(meet_id, coach_name)
);

CREATE TABLE IF NOT EXISTS coaches (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS competition_coaches (
    competition_id TEXT NOT NULL
        REFERENCES competitions(id) ON DELETE CASCADE,
    coach_id TEXT NOT NULL REFERENCES coaches(id) ON DELETE RESTRICT,
    role TEXT NOT NULL DEFAULT 'coach'
        CHECK (role IN ('coach', 'coordinator', 'admin')),
    added_at TEXT NOT NULL,
    PRIMARY KEY(competition_id, coach_id, role)
);

CREATE TABLE IF NOT EXISTS day_coach_presence (
    meet_id TEXT NOT NULL REFERENCES meets(id) ON DELETE CASCADE,
    coach_id TEXT NOT NULL REFERENCES coaches(id) ON DELETE RESTRICT,
    presence_status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (presence_status IN ('scheduled', 'present', 'absent')),
    home_event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(meet_id, coach_id)
);

CREATE TABLE IF NOT EXISTS coach_assignment_history (
    id BIGSERIAL PRIMARY KEY,
    competition_id TEXT REFERENCES competitions(id) ON DELETE SET NULL,
    meet_id TEXT REFERENCES meets(id) ON DELETE SET NULL,
    event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
    athlete_id TEXT REFERENCES athletes(id) ON DELETE SET NULL,
    coach_id TEXT REFERENCES coaches(id) ON DELETE SET NULL,
    coach_name TEXT NOT NULL,
    assignment_kind TEXT NOT NULL
        CHECK (assignment_kind IN ('main', 'side', 'coverage', 'de_coach')),
    target_type TEXT NOT NULL
        CHECK (target_type IN ('athlete', 'pod')),
    phase TEXT NOT NULL DEFAULT '',
    pod TEXT NOT NULL DEFAULT '',
    home_event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
    is_cross_event INTEGER NOT NULL DEFAULT 0
        CHECK (is_cross_event IN (0, 1)),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    assigned_by TEXT NOT NULL DEFAULT '',
    ended_by TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS actions (
    id BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    athlete_id TEXT REFERENCES athletes(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    previous_json TEXT,
    new_json TEXT,
    version_after INTEGER,
    created_at TEXT NOT NULL,
    undone_at TEXT,
    undone_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_athletes_event
    ON athletes(event_id, active_state, phase, pod);
CREATE INDEX IF NOT EXISTS idx_actions_event
    ON actions(event_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_meet_events_order
    ON meet_events(meet_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_coach_availability_meet
    ON coach_availability(meet_id, is_available);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_pool_wave
    ON pool_waves(event_id) WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS idx_pool_waves_order
    ON pool_waves(event_id, sort_order, wave_key);
CREATE INDEX IF NOT EXISTS idx_competition_coaches
    ON competition_coaches(competition_id, role);
CREATE INDEX IF NOT EXISTS idx_day_coach_presence
    ON day_coach_presence(meet_id, presence_status);
CREATE INDEX IF NOT EXISTS idx_assignment_history_athlete
    ON coach_assignment_history(athlete_id, started_at, id);
CREATE INDEX IF NOT EXISTS idx_assignment_history_coach
    ON coach_assignment_history(coach_id, coach_name, started_at, id);
CREATE INDEX IF NOT EXISTS idx_assignment_history_open
    ON coach_assignment_history(event_id, target_type, assignment_kind)
    WHERE ended_at IS NULL;

-- Additive compatibility for earlier CompCoach cloud installations.
ALTER TABLE competitions ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'closed'));
ALTER TABLE competitions ADD COLUMN IF NOT EXISTS ended_at TEXT;
ALTER TABLE competitions ADD COLUMN IF NOT EXISTS ended_by TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_meets_competition_day
    ON meets(competition_id, competition_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_meets_prepared_from
    ON meets(prepared_from_meet_id) WHERE prepared_from_meet_id IS NOT NULL;
-- The one-active-day index is installed by shared bootstrap after legacy repair.

REVOKE ALL ON SCHEMA compcoach FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA compcoach FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA compcoach FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA compcoach REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA compcoach REVOKE ALL ON SEQUENCES FROM PUBLIC;
DO $compcoach_permissions$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON SCHEMA compcoach FROM anon';
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA compcoach FROM anon';
        EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA compcoach FROM anon';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA compcoach REVOKE ALL ON TABLES FROM anon';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA compcoach REVOKE ALL ON SEQUENCES FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON SCHEMA compcoach FROM authenticated';
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA compcoach FROM authenticated';
        EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA compcoach FROM authenticated';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA compcoach REVOKE ALL ON TABLES FROM authenticated';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA compcoach REVOKE ALL ON SEQUENCES FROM authenticated';
    END IF;
END
$compcoach_permissions$;


-- Additive upgrade for existing deployments; keep intentional empty groups.
ALTER TABLE athletes ADD COLUMN IF NOT EXISTS de_coaches_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE athletes ADD COLUMN IF NOT EXISTS de_byes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE athletes ADD COLUMN IF NOT EXISTS takeover_coach TEXT NOT NULL DEFAULT '';
ALTER TABLE athletes ADD COLUMN IF NOT EXISTS takeover_at TEXT;
ALTER TABLE athletes ADD COLUMN IF NOT EXISTS takeover_by TEXT NOT NULL DEFAULT '';
-- Backfill once: a later explicit "ready" choice must survive app restarts.
DO $compcoach_de_round_upgrade$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'athletes'
          AND column_name = 'de_awaiting_next'
    ) THEN
        ALTER TABLE athletes ADD COLUMN de_awaiting_next INTEGER NOT NULL DEFAULT 0
            CHECK (de_awaiting_next IN (0, 1));
        UPDATE athletes SET de_awaiting_next = 1
        WHERE phase = 'de' AND active_state = 'active'
          AND last_de_result IN ('won', 'bye')
          AND call_status = 'waiting' AND covered_by = ''
          AND help_requested_at IS NULL;
    END IF;
END
$compcoach_de_round_upgrade$;
ALTER TABLE pod_assignments ADD COLUMN IF NOT EXISTS coaches_json TEXT NOT NULL DEFAULT '[]';
UPDATE athletes SET de_coaches_json = json_build_array(main_coach, side_coach)::text
WHERE phase = 'de' AND de_coaches_json = '[]' AND (main_coach <> '' OR side_coach <> '');
UPDATE pod_assignments SET coaches_json = json_build_array(main_coach, side_coach)::text
WHERE phase = 'de' AND coaches_json = '[]' AND (main_coach <> '' OR side_coach <> '');
ALTER TABLE coach_assignment_history DROP CONSTRAINT IF EXISTS coach_assignment_history_assignment_kind_check;
ALTER TABLE coach_assignment_history ADD CONSTRAINT coach_assignment_history_assignment_kind_check
    CHECK (assignment_kind IN ('main', 'side', 'coverage', 'de_coach'));
UPDATE coach_assignment_history SET assignment_kind = 'de_coach'
WHERE phase = 'de' AND assignment_kind IN ('main', 'side');
CREATE TABLE IF NOT EXISTS de_bouts (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    athlete_a_id TEXT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
    athlete_b_id TEXT NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
    round_label TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'resolved', 'cancelled')),
    winner_id TEXT NOT NULL DEFAULT '',
    athlete_a_version INTEGER NOT NULL,
    athlete_b_version INTEGER NOT NULL,
    resolved_a_version INTEGER,
    resolved_b_version INTEGER,
    previous_a_json TEXT,
    previous_b_json TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_de_bouts_event_status ON de_bouts(event_id, status);
