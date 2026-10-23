
<!-- rein-rules -->
## Loose Rein
This repo uses Loose Rein (Human-on-the-Loop, gated delta cycles). Claude Code realizes
the rules' capability vocabulary as: `phase-invocation` → the /req … /status slash
commands; `structured-question` → AskUserQuestion; `notify-and-wait` → PushNotification;
`approval-presentation` → plan mode + ExitPlanMode; `session-compaction` → /compact
(human-run); `role-delegation` → the subagents in .claude/agents/;
`command-preauthorization` → permissions.allow in
.claude/settings.json; `background-wait` → Bash with run_in_background (the run's exit
re-invokes you). The implementation phase is `rein build` — one command whose completion
is the signal, so wait for it and never poll it. The operating rules are imported from:
@.rein/AGENTS.rein.md
