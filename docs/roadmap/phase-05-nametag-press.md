# Phase 5 — `nametag-press`

Print-ready Avery badge sheets from the registrant roster.

**Depends on:** Phase 1 (`auth`, `db`, `backup`), Phase 2.
**Design detail:** [`PLAN.md`](PLAN.md) §G.3.

## Why before `lodging-planner`

Both come out of the same `posted` repo and both read the same `Registrant` table
today, so splitting them is the awkward part of the whole extraction. Do the
read-mostly one first: `nametag-press` takes registrants and produces PDFs, with no
concurrency story and no rules engine. That gets the `posted` split done once, and
leaves `lodging-planner` as a clean second pass.

## Scope

ReportLab badge generation, three Avery templates, auto-shrinking name and
affiliation text, role → label + colour from the profile, logo upload, blank-sheet
printing, and a roster table with role filters and tallies.

Model, routes, and geometry table are in `PLAN.md` §G.3.

## Decision: drop the browser-print path

Geometry and card content are currently defined **twice** — ReportLab
(`main.py:937-976`, `:1051-1118`) and print CSS/JS (`admin_nametags.html:213-330`,
`:947-983`, `:1203-1214`). They will diverge on the first tweak, and the CSS version
already cannot reproduce ReportLab's per-line autoshrink, so a long name prints
differently in the two paths. That is precisely the failure that ruins a sheet of
Avery stock.

Keep one renderer. Replace browser printing with an in-browser PDF preview
(`<iframe src="/api/badges.pdf#toolbar=0">`) plus the browser's own print dialog —
staff keep "see it before you print", you keep one geometry.

`layouts.json` is **generated from `layout.py`** and asserted equal in CI, so JS can
still draw the on-screen selection grid without owning dimensions.

## Decision: this app does not own swag

`t_shirt_size` on `posted`'s `Registrant` (`models.py:29`) is stored, backed up, and
**never rendered anywhere** — dead weight. Inventory, replacement and issuance belong
where the check-in desk is, which is `ticket-reconciler`. Drop the column.

If an event wants a size printed on the badge, that is a profile setting referencing
a roster value, not a second inventory system. Two apps counting shirts is how you
oversell mediums.

## Logos move into the database

Uploads currently land in `frontend/static/images/badge_{slot}_custom.{ext}`
(`main.py:342-370`), which is **not** the Azure Files mount — so they vanish on
container restart, and the PDF generator then silently draws nothing because
`main.py:1038-1039` is a bare `except: pass`.

Store bytes in a `BrandingAsset` table keyed by slot. Fix the bare except while you
are there: a corrupt SVG should degrade visibly, not silently.

## Avery geometry

| Template | Card | Grid | Padding / gaps | Name / affiliation pt |
|---|---|---|---|---|
| `74541` | 4.0 × 3.0 in | 2 × 3 | pad 1.0 in top/bottom, 0.25 in sides; gap 0 | 22 / 11–12 |
| `5392` (= 74536) | 4.0 × 3.0 in | 2 × 3 | identical to 74541 | 22 / 12 |
| `5395` | 3.375 × 2.33 in | 2 × 4 | pad 0.5 in top/bottom, 0.75 in sides; gaps 0.1 × 0.25 in | 16 / 9–10 |

Sheet is 8.5 × 10.95 in with `page-break-after: always`.

## Tests

- `LAYOUTS["74541"]` and `["5392"]` identical; `["5395"]` matches its row above.
- **Every layout fits letter**: `margin*2 + cols*w + (cols-1)*gap <= 8.5in`.
- `fit_text` monotonic; floors at 12pt from a 22 start and 10pt from 16; affiliation
  floors at 8pt.
- Page count = `ceil(n / cards_per_page)`; blank sheets 1..N.
- An SVG sponsor logo parses; a **corrupt** SVG degrades without a 500.
- Role → colour from the profile; unknown role → neutral.
- PDF output starts `%PDF` with the expected page count (via `pypdf`).
- vitest: `layouts.json` deep-equals a fixture exported from `layout.py`; role
  tallies; filter chips; preview iframe URL encodes the selected keys.

## Acceptance criteria

- [ ] One renderer. No CSS print grid owning dimensions.
- [ ] `layouts.json` generated and CI-asserted against `layout.py`.
- [ ] `t_shirt_size` absent from the model.
- [ ] Logos survive a container restart.
- [ ] Every template's geometry test passes, including the fits-on-letter assertion.
- [ ] A physical calibration print checked against real Avery stock before sign-off.

## Risks

**Geometry bugs cost money and are invisible in CI.** A passing test suite and a
misaligned sheet are compatible. Print one blank sheet on plain paper and hold it
against real stock — that check is in the runbook for a reason.

**Splitting `posted` is the risky part, not the PDFs.** After this phase,
`nametag-press` and `lodging-planner` have separate databases fed by separate
handlers. A lodging write-in will **not** appear in nametags. That is the accepted
cost of independent databases; document it, and ship the
`nametag-press import --from lodging-backup.json` bridge plus
`identity-drift --against <other-backup.json>` so staff can see divergence *before*
badges are printed.
