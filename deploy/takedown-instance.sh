#!/usr/bin/env bash
#
# Take down a user's Instance — cascade-delete its database records (boards,
# workspaces, members, provisioned users, per-user token usage).
#
# NOT removed by this: Cognito accounts (a separate step — the CLI prints the
# admin-delete-user commands) and the append-only JSONL audit/chat logs (retained
# as the security trail).
#
# The DB delete runs inside the live container (via ECS Exec) so it reaches RDS.
# The in-container CLI prints the Instance's roster and asks you to re-type the id
# to confirm — type it into the session. It is a hard delete; there is no undo.
#
# Usage:
#   ./takedown-instance.sh                       # lists Instances so you can find the id
#   ./takedown-instance.sh instance_ab12cd34ef56 # take that Instance down
#
# Cognito accounts are NOT deleted here (they may be reused). The CLI prints the
# exact `admin-delete-user` command per member if you also want to remove them.
#
set -euo pipefail
cd "$(dirname "$0")"; . ./config.sh

IID="${1:-}"
if [ -z "$IID" ]; then
  warn "No instance id given — here is the current roster. Re-run with the id you want gone."
  backend_exec "python -m app.admin list-instances"
  exit 0
fi

warn "This permanently deletes Instance '$IID' and everything in it. There is no undo."
backend_exec "python -m app.admin takedown-instance '$IID'"
say "If you also want to remove their Cognito logins, run the admin-delete-user commands printed above."
