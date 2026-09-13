CREATE SCHEMA IF NOT EXISTS fzh;

CREATE TABLE IF NOT EXISTS fzh.system_metadata (
    key text PRIMARY KEY,
    value jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO fzh.system_metadata (key, value)
VALUES ('schema', '{"name":"frankensteinzhermes","version":1}'::jsonb)
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value,
    updated_at = now();
