-- Append-only trigger (installed by alembic migration 0001).
-- Keep in sync with migrations/versions/0001_init.py.

CREATE OR REPLACE FUNCTION paykeeper_ledger_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entries is append-only (op=%)', TG_OP
        USING ERRCODE = 'raise_exception';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_entries_append_only
BEFORE UPDATE OR DELETE ON ledger_entries
FOR EACH ROW
EXECUTE FUNCTION paykeeper_ledger_append_only();
