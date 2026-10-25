# Shared test vectors

The web app, the Android apps and the server each implement some rules twice or three times: the
quick-add grammar, the checklist item grammar, the citation markers, mailto: links and others. The
files here hold the cases that every implementation must pass.

| Folder | Contents | Run by |
|---|---|---|
| `cases/` | `*-cases.yaml`: grammar and layout vectors | vitest and JUnit; pytest too for the citation, item and table-view cases |
| `fixtures/` | the assistant's presentation blocks (schema and golden fixtures) and the label colour schemes | vitest, JUnit and pytest |

The YAML files use one restricted shape (`key: <json>` lines, `  - <json>` cases, `#` comments), so
each test runner reads them without a YAML library.

The Android tests read copies in `android/app/src/test/resources/`. The tests check that the copies
match the files here, so a change goes into both places.

`label-colours.py` builds `fixtures/label-colours.json` and its copies from OKLCH parameters. Run
it from the repository root after a change: `python3 spec/label-colours.py`.
