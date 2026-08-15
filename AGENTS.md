# Repository rules

- Discuss and approve design before implementation.
- Use `uv` for Python dependencies and `just` for developer commands.
- Run `just check` before handoff.
- Work only in feature branches and deliver changes through pull requests.
- Never push directly to `main` or `master`.
- Keep secrets in `.env`; never commit `.env` or Telegram tokens.
- Do not add paid AI/API calls without explicit approval.
- Prefer clear explanations that help the maintainer learn the reasoning.
