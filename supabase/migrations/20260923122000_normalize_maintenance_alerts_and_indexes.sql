ALTER TABLE public.maintenance_alerts
    ALTER COLUMN status SET DEFAULT 'pending_confirmation';

ALTER TABLE public.maintenance_alerts
    ADD CONSTRAINT maintenance_alerts_status_check
    CHECK (
        status IN (
            'pending_confirmation',
            'confirmed',
            'integrator_notified',
            'resolved',
            'expired'
        )
    ) NOT VALID;

ALTER TABLE public.maintenance_alerts
    VALIDATE CONSTRAINT maintenance_alerts_status_check;

DROP INDEX IF EXISTS public.idx_daily_generation_usina_data;

DROP INDEX IF EXISTS public.idx_daily_weather_usina_data;
ALTER INDEX IF EXISTS public.idxdailyweatherusinadata
    RENAME TO idx_daily_weather_station_date;

DROP INDEX IF EXISTS public.idx_maintenance_alerts_usina_status;

DROP INDEX IF EXISTS public.idx_monthly_generation_usina_mes;

CREATE UNIQUE INDEX IF NOT EXISTS uq_maintenance_alerts_open
    ON public.maintenance_alerts (provider, station_id, alert_type)
    WHERE status IN ('pending_confirmation', 'confirmed', 'integrator_notified');
