# Company substrate: what Claude Code already provides (survey, 2026-11-21)

Purpose: before building any machinery for the "company" swarm (continuing specialist agents with durable state that build this software as peers), record what is native and what is not. Rule from the owner: build only what is missing.
Sources: official docs read by a documentation-research agent (urls below; "documented"), plus things observed in this very session ("observed here"). Anything else is marked "inferred".

## Native mechanisms

| Need | Native mechanism | Status |
|---|---|---|
| Separate context per worker | Subagents (Agent tool); agent-team teammates | Subagents: observed here (5+ contexts, hundreds of tool calls each, own token counts in completion notices). Teammates: documented only. |
| Worker continuity within a session | Resume a finished subagent by `SendMessage` to its name/id; full history kept | Observed here: the two area leads were resumed 10+ times and kept context (e.g. H-lead reviewing S-lead's module two batches later). Documented: needs no agent-teams flag. |
| Peer messaging | `SendMessage` between subagents / sessions; `ListAgents` | Observed here: H-lead and S-lead exchanged review findings directly, delivered at the receiver's next tool round. Cross-session messaging (v2.1.224+) documented. |
| Hierarchy | Subagents can spawn subagents, default depth 3 | Observed here: lead -> engineer worked (S-lead spawned two engineers; H-lead one). |
| Per-worker durable memory | Custom agent file `.claude/agents/<name>.md` with `memory: project` -> `.claude/agent-memory/<name>/MEMORY.md`, first 200 lines / 25 KB loaded into the worker's prompt | Documented, NOT yet exercised here. |
| Shared task list + mailbox | Agent teams (experimental): `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`; tasks in `~/.claude/tasks/{team}/`, inboxes in `~/.claude/teams/{team}/inboxes/` | Documented. NOT available in this session: the flag is unset and the session has no team tools. |
| Project-wide shared instructions/state | CLAUDE.md, auto memory (`~/.claude/projects/<project>/memory/`), the git repo | Documented; the repo is already the durable shared store (docs, records, tests). |
| Lifecycle / nudge logging | Hooks: `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `SessionStart`, `Stop`, `TeammateIdle`, `TaskCreated`, `TaskCompleted`; hook input carries session id, transcript path, agent id | Documented, NOT yet exercised here. |
| Per-worker records | Subagent transcripts (`~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl`), kept 30 days by default | Observed here: files exist for this session's subagents. Format is internal and may change. |

## Documented limits that matter
* Agent-team teammates are NOT restored by `/resume` or `/rewind`; one team per session; teammates cannot nest; non-interactive `-p` sessions do not spawn teammates; teammate cost scales with count (subscription usage).
* Subagent state is tied to the parent session (inferred: after a restart there is no documented way to resume a subagent by id in a new session).
* A subagent the user stopped does not auto-resume.
* Cloud scheduled tasks start fresh from a clone, with no access to local state.

## What is proven vs missing for a "company"
Proven natively and observed: separate contexts; hierarchy; continuity within a session; direct peer messaging; per-worker usage numbers and transcripts.

Missing or unproven (and therefore the only things worth building):
1. **Durability across a session restart.** Worker contexts die with the session. Native pieces that could carry state over, but are unexercised: `memory: project` agent files and the repo itself. To test, not to assume.
2. **A shared task/message board that survives restarts and is visible to all peers.** The native one (agent-team task list) is unavailable here and is not restored across `/resume`. Until an experiment shows otherwise, a minimal repo-resident append-only board is the missing piece.
3. **An intervention ledger** (every external nudge, including the owner's, so supervision is never counted as autonomy). Nothing native records this as a first-class thing; `UserPromptSubmit` and `SubagentStart/Stop` hooks are the native hooks to feed it. Missing piece = the ledger file and its writer, nothing more.
4. **Peer autonomy evidence.** Today the project lead (this session) assigns every task and relays results; that is the "chatbot dispatching jobs" pattern. Native messaging is enough for peers to coordinate directly; what is missing is a protocol (who may pull which task) and evidence that peers actually used it without relay.

## Not built, on purpose
No STATE.md, no custom agent runtime, no custom messaging transport, no copy of a task list that agent teams provide natively.

Sources: code.claude.com/docs/en/{agent-teams,sub-agents,hooks,memory,worktrees,sessions,cross-session-messaging,routines}.md. Documented limits were not independently verified, except where marked "observed here".
