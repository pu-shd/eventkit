# Roadmap

The remaining work, as a stack of pull requests you can read with `gh`.

This exists so the project can be picked up on another machine from a clone alone.
Everything needed is in the repository; nothing lives in a local scratch directory.

## Reading it on a fresh machine

```sh
gh repo clone pu-shd/eventkit && cd eventkit

gh pr list --state open --json number,title,headRefName,baseRefName \
  --jq '.[] | "#\(.number)  \(.title)   [\(.headRefName) → \(.baseRefName)]"' \
  | sort

gh pr view <n>              # the phase summary
gh pr diff <n>              # the phase document itself
```

Or just read the files, which is the same content:

```sh
ls docs/roadmap/
```

## The stack

Each PR is based on the previous one, so the diffs stay small and the order is
explicit. Merge them in order, or merge the lot into `main` at once — they only
add documentation, so nothing breaks either way.

| # | Branch | Phase | Depends on |
|---|---|---|---|
| 1 | `roadmap/00-index` | This index, the committed plan, current status | `main` |
| 2 | `roadmap/01-eventkit-modules` | Finish the eventkit library: `auth`, `db`, `backup`, `notify`, `realtime`, `importer`, `mirror`, `admin`, `eventbrite.client`/`sync`, `ui` | 1 |
| 3 | `roadmap/02-azure-toolkit` | The zsh bootstrap toolkit: `deploy`/`resume`/`update`/`teardown` with polling manual-step gates | 2 |
| 4 | `roadmap/03-poster-gallery` | First app extraction — the proof case | 3 |
| 5 | `roadmap/04-ticket-reconciler` | Reconciliation, check-in, swag | 4 |
| 6 | `roadmap/05-nametag-press` | Avery badge PDFs | 5 |
| 7 | `roadmap/06-lodging-planner` | Rooms, rules engine, concurrency | 6 |
| 8 | `roadmap/07-link-forge` | Prefilled per-person links, stateless | 7 |
| 9 | `roadmap/08-content-repos` | `drupal-event-forms` and `event-stack` | 8 |

## Documents

| File | What it is |
|---|---|
| [`PLAN.md`](PLAN.md) | The full plan and design appendix, ~3,000 lines. The durable record: API surfaces, the event-profile schema, per-app route tables, the security-fix table. Redacted for publication — see the note at the top. |
| [`STATUS.md`](STATUS.md) | What is done, what is live, what still needs a human with console access. Read this first. |
| `phase-NN-*.md` | One per stack entry. Scope, deliverables, specs, tests, acceptance criteria, and pointers into `PLAN.md` for depth. |

## How the phase documents are meant to be used

Each is written to be executable without re-deriving anything:

- **Goal** and **why this order** — the dependency that fixes its position.
- **Prerequisites** — what must be true before starting.
- **Deliverables** — file tree and the API surface to build.
- **What moves from where** — `file:line` references into the two predecessor
  repositories, which are archived but still readable.
- **What to delete** — the event-specific values that must not survive.
- **Tests** — the cases that matter, including the ones with no coverage today.
- **Acceptance criteria** — a checklist that is either met or not.
- **Risks** — what has already gone wrong here, or is likely to.

`PLAN.md` holds the long-form design. The phase documents hold the plan of work.

## Ground rules that apply to every phase

1. **No event-specific value in application code.** It goes in
   `event-profile.yaml` or it does not ship. CI greps for the ones that leaked
   before.
2. **Tests with every change**, run in Docker so local and CI are the same
   command: `docker-compose run --rm test`.
3. **`create_app()` factory, no import-time side effects.** This is what lets an
   app's `conftest.py` be one line, and it is a per-app refactor.
4. **Fail closed.** Empty allow-list denies. Destructive operations are opt-in.
   Secrets are required, never defaulted.
5. **Read the security table in `PLAN.md` before touching an app.** Every entry
   was verified against source; the two worst were live and anonymous.
