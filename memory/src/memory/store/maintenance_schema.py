"""Additive maintenance metadata, kept in the same transaction domain as Semantic."""

DDL = (
    """CREATE TABLE IF NOT EXISTS semantic_audit (
        id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, session_id TEXT NOT NULL,
        scope_json TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS semantic_audit_item (
        audit_id TEXT NOT NULL REFERENCES semantic_audit(id), semantic_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL, snapshot_json TEXT NOT NULL, revision TEXT NOT NULL,
        read_at TEXT, decision_json TEXT, reviewed_at TEXT,
        PRIMARY KEY(audit_id, semantic_id), UNIQUE(audit_id, ordinal)
    )""",
    """CREATE TABLE IF NOT EXISTS semantic_maintenance_batch (
        id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, session_id TEXT NOT NULL,
        audit_id TEXT NOT NULL REFERENCES semantic_audit(id), digest TEXT NOT NULL,
        payload_json TEXT NOT NULL, receipt_json TEXT, created_at TEXT NOT NULL,
        committed_at TEXT
    )""",
    """CREATE INDEX IF NOT EXISTS idx_semantic_audit_owner
        ON semantic_audit(owner_user_id, created_at)""",
)
