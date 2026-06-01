ALTER TABLE IF EXISTS incident
    DROP CONSTRAINT IF EXISTS incident_source_check;

ALTER TABLE IF EXISTS incident
    ADD CONSTRAINT incident_source_check
        CHECK (
            (source)::text = ANY (
                (
                    ARRAY[
                        'PROMETHEUS'::character varying,
                        'ALERTMANAGER'::character varying,
                        'AI'::character varying,
                        'MANUAL'::character varying,
                        'OTHER'::character varying
                    ]
                )::text[]
            )
        );
