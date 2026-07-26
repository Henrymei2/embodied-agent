# Skills

## What a skill is

A skill is a procedure the agent writes for itself: what it is for, when it
applies, and the steps. Approved skills are retrieved into the system prompt, so
a skill genuinely changes how the agent behaves later — which is precisely why
the agent cannot approve one.

This is the project's self-evolution path. It stops short of the agent editing
executable code unattended, and instead lets it propose changes to its own
instructions that a human commits.

## The loop

1. The agent walks to the `tool rack` and calls `draft_skill`.
2. The draft appears in the rack's hover window with Approve and Decline.
3. On approval the file moves into `skills/`, where retrieval picks it up. From
   the next turn on, the skill is part of the agent's instructions.
4. `revise_skill` produces a new pending version. An active skill keeps applying
   until its revision is approved, so review never leaves a gap.
5. `retire_skill` needs no approval: it only ever removes a capability.

Declined drafts stay in `skills/drafts/` so they can be revised rather than
rewritten from scratch.

## Where they live

| Path | Meaning | Retrieved into the prompt |
| --- | --- | --- |
| `<workspace>/skills/<slug>.md` | approved and active | yes |
| `<workspace>/skills/drafts/<slug>.md` | pending or declined | no |
| `<workspace>/skills/retired/<slug>.md` | withdrawn by the agent | no |

Each file is markdown with a small YAML frontmatter block (name, status,
purpose, when_to_use, revision, timestamps). A skill's *location* decides its
status, so editing `status:` in a draft's frontmatter changes nothing.

## Why the gate holds

Approval is only meaningful if the agent cannot route around it, and the agent
can otherwise write freely in its own workspace. Three things close that path:

- `record_skill_decision` is reached only from `POST /api/furniture/skills/{slug}/decision`,
  which the rack's window calls. No MCP tool can activate a skill.
- The file tools (`write_file`, `append_to_file`, `edit_file`, `create_directory`,
  `delete_path`, `move_path`) refuse any path under `skills/`.
- `execute_bash` runs in a container where `/workspace/skills` is bind-mounted
  read-only, so shell redirection cannot write there either.

The retriever additionally skips `skills/drafts/` and `skills/retired/`, so an
unapproved procedure never reaches the prompt even if a file lands there by
other means.

## Tools

All five require standing at the `tool rack`.

| Tool | Effect |
| --- | --- |
| `draft_skill(name, purpose, when_to_use, steps, notes)` | new pending draft |
| `list_skills(status)` | names, statuses, and triggers |
| `get_skill(slug)` | one skill in full |
| `revise_skill(slug, ...)` | new pending revision; bumps `revision` |
| `retire_skill(slug, reason)` | moves an active skill out of use |

Limits: 100 skills, 15 steps each, 300 characters per step, and names are
slugified to `[a-z0-9-]` with a 60-character cap.

## Autonomy

A growth-direction candidate prompts the agent, when it has been idle a while,
to look back at something it worked out and draft a skill for it. Because the
result is a pending draft, an autonomous impulse to change its own behavior
still lands in front of the user before it takes effect.
