CREATE TABLE IF NOT EXISTS public.solis_alarm_events (
    id BIGSERIAL PRIMARY KEY,
    station_id TEXT NOT NULL,
    device_sn TEXT NOT NULL DEFAULT '',
    alarm_code TEXT NOT NULL,
    alarm_level INTEGER,
    alarm_message TEXT,
    advice TEXT,
    alarm_begin_time TEXT NOT NULL,
    alarm_end_time TEXT,
    state INTEGER,
    warning_info_data JSONB,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'resolved')),
    notified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (station_id, device_sn, alarm_code, alarm_begin_time)
);

CREATE INDEX IF NOT EXISTS idx_solis_alarm_events_active
    ON public.solis_alarm_events (station_id, status)
    WHERE status = 'active';

ALTER TABLE public.solis_alarm_events
    ENABLE ROW LEVEL SECURITY;
