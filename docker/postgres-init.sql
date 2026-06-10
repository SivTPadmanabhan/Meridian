-- Runs once on first postgres container init (mounted into
-- /docker-entrypoint-initdb.d/). The POSTGRES_DB env var already creates the
-- "meridian" database; here we add the separate "langfuse" database.
-- The pgvector extension is created inside the meridian DB by the Alembic
-- initial migration, not here.

SELECT 'CREATE DATABASE langfuse'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'langfuse')\gexec
