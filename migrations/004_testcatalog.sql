-- 004_testcatalog.sql
-- Add source discriminator to openqa_tests so TestCatalog and openQA data coexist.
-- Idempotent: ADD COLUMN IF NOT EXISTS + DROP CONSTRAINT IF EXISTS.

ALTER TABLE openqa_tests
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'openqa';

ALTER TABLE openqa_tests
    DROP CONSTRAINT IF EXISTS openqa_tests_package_id_test_path_key;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'openqa_tests_package_id_test_path_source_key') THEN
        ALTER TABLE openqa_tests
            ADD CONSTRAINT openqa_tests_package_id_test_path_source_key
            UNIQUE (package_id, test_path, source);
    END IF;
END $$;
