-- W0b — L1–L4 approval ticket flow (FULL-SYSTEM-BLUEPRINT §6, C8).
--
-- Split of responsibilities (append-only D4, mutable registry D3):
--   d3.tickets   — ticket registry: what was requested, at what tier, by whom.
--                  Mutable reference data: a ticket may be withdrawn (DELETE)
--                  BEFORE any decision; once decided, the decision row in D4
--                  is immutable and the ticket row is kept for the audit JOIN.
--   d4.approvals — decisions ONLY (grant|deny, CHECK-enforced). Append-only.
--
-- Derived state: a d3.tickets row with NO matching d4.approvals row is
-- `pending`. Ticket+decision JOIN is the audit view below.
--
-- Tier semantics (approval authority):
--   L1 sandbox checks — any registered seat role may decide (automated).
--   L2+ sign-offs / L3 live-decisions / L4 tier — ONLY the owner role decides.
--   C8 quota — at most 5 L3 grants per rolling 7 days; enforcement is in the
--   decision path (fail-closed exit 7), verified by gate G8.

CREATE TABLE IF NOT EXISTS d3.tickets (
    ticket         TEXT PRIMARY KEY,
    level          TEXT        NOT NULL CHECK (level IN ('L0','L1','L2','L3','L4')),
    requested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    requester_role TEXT        NOT NULL REFERENCES d3.roles(role_name),
    reason         TEXT        NOT NULL
);

CREATE OR REPLACE VIEW d3.approval_state AS
SELECT t.ticket,
       t.level,
       t.requester_role,
       t.reason,
       (a.appr_id IS NULL) AS pending,
       a.decision,
       a.role_name         AS decider_role,
       a.appr_id
FROM d3.tickets t
LEFT JOIN d4.approvals a ON a.ticket = t.ticket
ORDER BY t.ticket;
