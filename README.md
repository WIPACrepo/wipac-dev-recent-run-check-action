# WIPACrepo/wipac-dev-recent-run-check-action
This GitHub Action checks whether the **current workflow or job** has already run on the **same branch**, for a **different commit**, within the last *N seconds*.  
It outputs a single boolean called `ran_recently`.

---

## 💡 How It Works

| Case | Output: `ran_recently` |
|------|--------------------------|
| First workflow run on branch | `false` |
| Another commit triggers workflow within N seconds | `true` |
| Previous run is older than N seconds | `false` |
| Same commit re-run (manual re-run) | `false` |
| On default branch & `always-false-on-default-branch: true` | `false` |

This allows you to **skip expensive workflow steps if the branch was already built recently**, without cancelling the run entirely.

---

## ✅ Inputs

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `scope` | `"job"` or `"workflow"` | `"job"` | Whether to check at the job level or workflow-level. |
| `window-seconds` | integer | `600` | Number of seconds that counts as “recent”. |
| `always-false-on-default-branch` | `"true"` / `"false"` | `"true"` | If true, always returns `false` when running on the repo’s default branch. |

---

## ✅ Output

| Output | Description |
|--------|-------------|
| `ran_recently` | `"true"` or `"false"` |

---

## 📁 Example Usage (`.github/workflows/ci.yml`)

```yaml
name: CI

on:
  push:
    branches: ["**"]

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Check if job/workflow ran recently
        id: recent
        uses: your-org/recent-run-check@v1
        with:
          scope: job                    # or 'workflow'
          window-seconds: 900           # 15 minutes
          always-false-on-default-branch: true

      - name: Skip if too recent
        if: ${{ steps.recent.outputs.ran_recently == 'true' }}
        run: echo "Skipping — ran recently."

      - name: Run build
        if: ${{ steps.recent.outputs.ran_recently != 'true' }}
        run: |
          echo "Proceeding with build..."
          make all
```
