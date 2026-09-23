-- Least-privilege user for the database MCP server (Architecture.md §6.2 / §14).
-- The app connects as "foreman"; the agents' query tool connects as "foreman_ro" and can only SELECT.
CREATE ROLE foreman_ro WITH LOGIN PASSWORD 'foreman_ro';
GRANT CONNECT ON DATABASE foreman TO foreman_ro;
GRANT USAGE ON SCHEMA public TO foreman_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO foreman_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO foreman_ro;
-- Belt and braces: even if a write slips past the gate and the SQL guard, the session is read-only.
ALTER ROLE foreman_ro SET default_transaction_read_only = on;
