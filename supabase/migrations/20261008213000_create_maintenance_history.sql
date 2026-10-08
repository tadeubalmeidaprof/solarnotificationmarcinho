CREATE TABLE IF NOT EXISTS maintenance_history (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    station_id TEXT NOT NULL,
    event_date DATE NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'cleaning',
            'inspection',
            'preventive',
            'corrective',
            'repair',
            'replacement',
            'electrical',
            'inverter',
            'panel',
            'other'
        )
    ),
    description TEXT NOT NULL,
    performed_by TEXT,
    notes TEXT,
    source TEXT NOT NULL DEFAULT 'whatsapp',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_maintenance_history_station_date
    ON maintenance_history (provider, station_id, event_date DESC, id DESC);

ALTER TABLE maintenance_history ENABLE ROW LEVEL SECURITY;
