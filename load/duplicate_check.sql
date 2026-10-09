-- Post-load invariant check. All of these must return zero rows.

\echo '[1] charge rows per operation_id > 1 (should be impossible via UNIQUE):'
SELECT operation_id, COUNT(*)
FROM charges
GROUP BY operation_id
HAVING COUNT(*) > 1;

\echo '[2] duplicate ledger entries for a single successful charge:'
SELECT charge_id, COUNT(*)
FROM ledger_entries
WHERE entry_type = 'charge.succeeded'
GROUP BY charge_id
HAVING COUNT(*) > 1;

\echo '[3] completed idempotency keys with multiple matching charges:'
SELECT k.key, k.scope, COUNT(c.*)
FROM idempotency_keys k
JOIN charges c ON c.id = k.resource_id
WHERE k.state = 'completed'
GROUP BY k.key, k.scope
HAVING COUNT(c.*) > 1;
