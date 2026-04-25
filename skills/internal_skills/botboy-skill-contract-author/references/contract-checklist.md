# Contract Checklist

Use this checklist before calling a BotBoy-oriented skill folder "ready":

1. Confirm the folder name is lowercase hyphen-case.
2. Confirm `SKILL.md` exists and the frontmatter contains only `name` and `description`.
3. Confirm the description names both the capability and the trigger contexts.
4. Confirm the body is procedural, short, and imperative.
5. Confirm `agents/openai.yaml` exists and the default prompt explicitly names the skill.
6. Confirm `scripts/` contains only helpers that are deterministic or repeatedly useful.
7. Confirm `references/` contains deeper material instead of bloating `SKILL.md`.
8. Confirm file paths and examples point at the canonical BotBoy dev tree when repo-specific guidance is needed.
9. Confirm no stray README, changelog, or process diary was added to the skill folder.
10. Confirm the skill is precise enough that another agent could use it without hidden context.
