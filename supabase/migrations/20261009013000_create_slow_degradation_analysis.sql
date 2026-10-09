CREATE TABLE IF NOT EXISTS slow_degradation_analysis (
    provider TEXT NOT NULL,
    station_id TEXT NOT NULL,
    analysis_date DATE NOT NULL,
    algorithm_version TEXT NOT NULL,
    window_days INTEGER NOT NULL CHECK (
        window_days >= 45 AND window_days <= 366
    ),
    observations INTEGER NOT NULL DEFAULT 0,
    date_span_days INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    degradation_likelihood_percent NUMERIC(8, 3),
    confidence TEXT NOT NULL,
    confidence_score_percent NUMERIC(8, 3),
    estimated_recent_loss_percent NUMERIC(10, 4),
    annualized_trend_percent NUMERIC(10, 4),
    dominant_factor TEXT,
    factors JSONB NOT NULL DEFAULT '[]'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        provider,
        station_id,
        analysis_date
    )
);

CREATE INDEX IF NOT EXISTS idx_slow_degradation_station_date
    ON slow_degradation_analysis (
        provider,
        station_id,
        analysis_date DESC
    );

CREATE INDEX IF NOT EXISTS idx_slow_degradation_likelihood
    ON slow_degradation_analysis (
        provider,
        station_id,
        degradation_likelihood_percent DESC
    );

ALTER TABLE slow_degradation_analysis ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE slow_degradation_analysis
    FROM anon, authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLE slow_degradation_analysis
    TO service_role;
