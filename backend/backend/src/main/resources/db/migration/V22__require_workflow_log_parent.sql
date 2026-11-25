-- Ensure every workflow log references an existing workflow.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM workflow_logs WHERE workflow_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'V22 aborted: workflow_logs.workflow_id contains NULL values';
    END IF;

    ALTER TABLE workflow_logs
        ALTER COLUMN workflow_id SET NOT NULL;
END $$;