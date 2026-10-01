CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Tables are created idempotently by backend/app/postgres_store.py after the backend starts.
-- This init file establishes the extension and leaves migrations owned by the application.
