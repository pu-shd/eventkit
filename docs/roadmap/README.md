# Roadmap

One document per phase, ordered by dependency. **Phases 0–7 are built**; phase 8
is in progress. The table below says where each one landed.

This exists so the project can be picked up on another machine from a clone alone.
Everything needed is in the repository; nothing lives in a local scratch directory.

## Reading it on a fresh machine

```sh
gh repo clone pu-shd/eventkit && cd eventkit
cat docs/roadmap/STATUS.md      # what is done, what needs a human — read first
ls docs/roadmap/                # one file per phase
```

The phase documents were reviewed as a stack of pull requests, now merged. To read
that history and the per-phase discussion:

```sh
gh pr list --state merged --search "Roadmap" --json number,title \
  --jq '.[] | "#\(.number)  \(.title)"' | sort -t'#' -k2 -n
gh pr view <n>
```

## The phases

Ordered by dependency. Each was one PR; the two superseded numbers are noted
because GitHub closes a PR when its base branch is deleted, and a PR closed that
way can be neither reopened nor retargeted.

| # | Document | Phase | Status | Where it landed |
|---|---|---|---|---|
| 0 | [`PLAN.md`](PLAN.md), [`STATUS.md`](STATUS.md) | Plan, index, status | done | PR #1 |
| 1 | [`phase-01-eventkit-modules.md`](phase-01-eventkit-modules.md) | Finish the eventkit library: `auth`, `db`, `backup`, `notify`, `realtime`, `importer`, `mirror`, `admin`, `eventbrite.client`/`sync`, `ui` | **done** | `v0.2.0` |
| 2 | [`phase-02-azure-toolkit.md`](phase-02-azure-toolkit.md) | The zsh bootstrap toolkit: `deploy`/`resume`/`update`/`teardown` with polling manual-step gates | **done** | `v0.3.0`, PR #15 |
| 3 | [`phase-03-poster-gallery.md`](phase-03-poster-gallery.md) | First app extraction — the proof case | **done** | [`poster-gallery`](https://github.com/pu-shd/poster-gallery) |
| 4 | [`phase-04-ticket-reconciler.md`](phase-04-ticket-reconciler.md) | Reconciliation, check-in, swag | **done** | [`ticket-reconciler`](https://github.com/pu-shd/ticket-reconciler) |
| 5 | [`phase-05-nametag-press.md`](phase-05-nametag-press.md) | Avery badge PDFs | **done** | [`nametag-press`](https://github.com/pu-shd/nametag-press) |
| 6 | [`phase-06-lodging-planner.md`](phase-06-lodging-planner.md) | Rooms, rules engine, concurrency | **done** | [`lodging-planner`](https://github.com/pu-shd/lodging-planner) |
| 7 | [`phase-07-link-forge.md`](phase-07-link-forge.md) | Prefilled per-person links, stateless | **done** | [`link-forge`](https://github.com/pu-shd/link-forge) |
| 8 | [`phase-08-content-repos.md`](phase-08-content-repos.md) | `drupal-event-forms` and `event-stack`, then archiving | in progress | — |

The phase documents are the **plans as written**, kept as the record of intent.
Where the built thing diverged from the plan, [`STATUS.md`](STATUS.md) says so
and why — read it alongside, not instead.

## Documents

| File | What it is |
|---|---|
| [`PLAN.md`](PLAN.md) | The full plan and design appendix, ~3,000 lines. The durable record: API surfaces, the event-profile schema, per-app route tables, the security-fix table. Redacted for publication — see the note at the top. |
| [`STATUS.md`](STATUS.md) | What is done, what is live, what still needs a human with console access. Read this first. |
| `phase-NN-*.md` | One per phase. Scope, deliverables, specs, tests, acceptance criteria, and pointers into `PLAN.md` for depth. |

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
