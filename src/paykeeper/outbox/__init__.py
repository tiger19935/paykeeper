"""Transactional outbox.

Domain events are written to `outbox_events` inside the same database
transaction as the state change they describe. A separate drain process
(`paykeeper outbox-drain`) publishes them in insertion order — here, to
stdout. Replacing stdout with Kafka/SNS/etc. is a one-function change
and deliberately out of scope.
"""
