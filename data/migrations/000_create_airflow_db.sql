-- Creates the Airflow metadata database alongside the app database.
-- Runs first (alphabetical order) via docker-entrypoint-initdb.d.
-- The default POSTGRES_DB (org_synapse) is already created by the entrypoint.
SELECT 'CREATE DATABASE airflow'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'airflow')
\gexec
