# W0a foundation · compose/
#
# Stack: Postgres (D3/D4) · QuestDB (D1) · NATS JetStream (D2) — see
# ../docs/gates/W0a.md for the five store gates that run against this stack.
#
# Development lifecycle:
#   cp .env.example .env   # then fill the two dev values
#   docker compose up -d
#   docker compose ps      # all three services must show (healthy)
#
# SECURITY (invariant R5/secrets rule): placeholder credentials in .env.example
# are DEV-ONLY. Production never reads them; the stack is booted with env from a
# secret store, not from any committed file. .env is git-ignored.
Named docker volumes (pgdata · qdbdata) hold store state; wipe them only via a drill.
