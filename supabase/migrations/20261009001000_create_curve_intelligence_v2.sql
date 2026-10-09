CREATE TABLE IF NOT EXISTS solar_curve_points (
    provider TEXT NOT NULL,
    station_id TEXT NOT NULL,
    report_date DATE NOT NULL,
    minute_of_day SMALLINT NOT NULL CHECK (
        minute_of_day >= 0 AND minute_of_day <= 1439
    ),
    measured_at TIMESTAMPTZ NOT NULL,
    power_w NUMERIC(16, 3) NOT NULL CHECK (power_w >= 0),
    normalized_power NUMERIC(12, 6),
    source TEXT NOT NULL,
    quality TEXT NOT NULL DEFAULT 'observed',
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        provider,
        station_id,
        report_date,
        minute_of_day
    )
);

CREATE INDEX IF NOT EXISTS idx_solar_curve_points_station_date
    ON solar_curve_points (
        provider,
        station_id,
        report_date DESC
    );

CREATE INDEX IF NOT EXISTS idx_solar_curve_points_minute_history
    ON solar_curve_points (
        provider,
        station_id,
        minute_of_day,
        report_date DESC
    );

ALTER TABLE solar_curve_points ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS daily_curve_analysis (
    provider TEXT NOT NULL,
    station_id TEXT NOT NULL,
    report_date DATE NOT NULL,
    algorithm_version TEXT NOT NULL,
    source TEXT NOT NULL,
    peak_power_kwp NUMERIC(12, 4),
    samples INTEGER NOT NULL DEFAULT 0,
    energy_kwh NUMERIC(16, 4),
    normalized_energy_hours NUMERIC(12, 5),
    active_hours NUMERIC(12, 4),
    peak_fraction NUMERIC(12, 6),
    generation_start_minute SMALLINT,
    generation_end_minute SMALLINT,
    shape_score NUMERIC(8, 3),
    energy_score NUMERIC(8, 3),
    peak_score NUMERIC(8, 3),
    interruption_score NUMERIC(8, 3),
    window_score NUMERIC(8, 3),
    volatility_score NUMERIC(8, 3),
    telemetry_score NUMERIC(8, 3),
    anomaly_score NUMERIC(8, 3),
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    persistence_ratio NUMERIC(8, 5),
    persistent_days INTEGER NOT NULL DEFAULT 0,
    comparable_days INTEGER NOT NULL DEFAULT 0,
    baseline_days_used INTEGER NOT NULL DEFAULT 0,
    weather_context TEXT,
    cloud_cover_percent NUMERIC(8, 3),
    rain_mm NUMERIC(10, 3),
    avg_temperature_c NUMERIC(8, 3),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        provider,
        station_id,
        report_date
    )
);

CREATE INDEX IF NOT EXISTS idx_daily_curve_analysis_station_date
    ON daily_curve_analysis (
        provider,
        station_id,
        report_date DESC
    );

CREATE INDEX IF NOT EXISTS idx_daily_curve_analysis_score
    ON daily_curve_analysis (
        provider,
        station_id,
        anomaly_score DESC
    );

ALTER TABLE daily_curve_analysis ENABLE ROW LEVEL SECURITY;
