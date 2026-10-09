"""Append-only money journal.

The ledger table is append-only in two places: no UPDATE or DELETE path
goes through the service layer, and a PostgreSQL BEFORE-trigger
(installed by migration 0001) rejects both operations at the row level.
Both the service rule and the trigger exist on purpose: the trigger
makes "no UPDATE" a database invariant, not just a developer habit.
"""
