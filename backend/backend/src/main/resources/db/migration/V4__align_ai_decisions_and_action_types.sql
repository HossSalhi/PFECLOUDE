ALTER TABLE IF EXISTS incident_decision
    DROP CONSTRAINT IF EXISTS incident_decision_decision_type_check;

ALTER TABLE IF EXISTS incident_decision
    ADD CONSTRAINT incident_decision_decision_type_check
        CHECK (
            (decision_type)::text = ANY (
                (
                    ARRAY[
                        'DETECT_INCIDENT'::character varying,
                        'DETECT_NOISE'::character varying,
                        'RECOMMEND_ACTION'::character varying,
                        'APPROVE'::character varying,
                        'REJECT'::character varying,
                        'ESCALATE'::character varying,
                        'DEFER'::character varying
                    ]
                )::text[]
            )
        );

ALTER TABLE IF EXISTS incident_action
    DROP CONSTRAINT IF EXISTS incident_action_type_check;

ALTER TABLE IF EXISTS incident_action
    ADD CONSTRAINT incident_action_type_check
        CHECK (
            (type)::text = ANY (
                (
                    ARRAY[
                        'NOTIFICATION'::character varying,
                        'REMEDIATION'::character varying,
                        'INVESTIGATION'::character varying,
                        'RESTART_POD'::character varying,
                        'SCALE_DEPLOYMENT'::character varying,
                        'ESCALATION'::character varying
                    ]
                )::text[]
            )
        );
