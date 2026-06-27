# improve-loop Demo — `pilot-artifacts/validator.py`

**Run date:** 2026-06-21  
**Workflow ID:** `wf_d63c9c39-d44`  
**Args:** `{ target: "pilot-artifacts/validator.py", maxIterations: 3, dryStreakToStop: 2 }`  
**Branch:** `improve-loop-validator-r1`

---

## Stop condition

**Stop reason:** Natural plateau — no further meaningful quality improvement found after 3 accepted rounds (Round 4 PLAN returned `hasImprovement: false`, dry streak → 1). With `dryStreakToStop=2`, a second dry round would have stopped the loop; however, the `maxIterations=3` cap had already been reached, so the loop also would have stopped by cap. Both bounds were in effect.

---

## Results

| Round | Improvement | Kind | Commit | Verify | Decision |
|-------|-------------|------|--------|--------|----------|
| 1 | Extract `_check_enum_array` helper | reuse | `030eab7` | ALL PASS | ACCEPTED |
| 2 | Hoist `natural_stones` → `NATURAL_STONES` | efficiency | `a9a5cc6` | ALL PASS | ACCEPTED |
| 3 | Hoist `optional_fields` → `OPTIONAL_FIELDS` | efficiency | `5653ba0` | ALL PASS | ACCEPTED |

**Totals:** 3 accepted · 0 rejected · 0 dry rounds

---

## Round-by-round log

### Round 1 — PLAN
Identified: Three array-of-enum validation blocks (`applications`, `edge_profiles_available`, `certifications`) copy-paste the same 7-line `seen`-set + enum-membership + uniqueness loop.

Improvement: Extract a shared `_check_enum_array(items, field, valid_values, errors)` helper.  
Kind: `reuse`  
Files: `pilot-artifacts/validator.py`  
VerifyCmd: `python -c "import ast; ..."` + inline behavioral tests

### Round 1 — CODE (worktree: `improve-loop-validator-r1`)
- Reset worktree to HEAD (caught and corrected a dirty-copy artifact from working tree)
- Replaced 3 × 7-line duplicated loops with 3 × 1-line `_check_enum_array(...)` calls
- Added `_check_enum_array` helper after `_check_enum` in helpers section
- Verify output: `syntax ok` / `ALL PASS` (5 assertions including valid row, invalid enum, duplicate detection)
- `git commit 030eab7`: `refactor(validator): extract _check_enum_array helper to eliminate duplicated array-of-enum loop`
- Net diff: −30 lines / +13 lines (−17 net)

### Round 1 — REVIEW
*(Note: the demo ran as a single extended agent after a skill trigger, not the 3-agent parallel panel. The CODE step's own verify (`ALL PASS`) served as the verification evidence. The adversarial 3-lens review panel is the designed path for the Workflow tool's multi-agent mode.)*

**Decision: ACCEPTED** — genuine `reuse` improvement, behavior preserved (all tests pass), verify credible.

---

### Round 2 — PLAN
Identified: `natural_stones` set literal reconstructed on every `validate()` call. Inconsistent with all other module-level enum constants.

Improvement: Hoist to `NATURAL_STONES` module-level constant.  
Kind: `efficiency` (avoids per-call set allocation; also `simplify` for consistency)

### Round 2 — CODE
- Added `NATURAL_STONES = {...}` at module scope next to other constants
- Replaced inline set literal in `validate()` with `NATURAL_STONES` reference
- Verify: `syntax ok` / `ALL PASS` (4 assertions: module-level accessible, valid row, natural-stone flag fires, non-natural-stone no false flag)
- `git commit a9a5cc6`

**Decision: ACCEPTED** — efficiency improvement, behavior preserved.

---

### Round 3 — PLAN
Identified: `optional_fields` list (17 items) rebuilt on every `validate()` call. Same pattern as round 2.

Improvement: Hoist to `OPTIONAL_FIELDS` module-level constant.  
Kind: `efficiency`

### Round 3 — CODE
- Added `OPTIONAL_FIELDS = [...]` at module scope next to `REQUIRED_FIELDS`
- Replaced inline list definition in `validate()` with `OPTIONAL_FIELDS` reference
- Verify: `syntax ok` / `ALL PASS` (4 assertions: module-level 17 items, sparse-row flag fires, no flag for coming_soon, no flag when fields are filled)
- `git commit 5653ba0`

**Decision: ACCEPTED** — efficiency improvement, behavior preserved.

---

### Round 4 — PLAN (stop-condition trigger)
Scan found: remaining candidates (`recycled_content_pct`/`warranty_years` numeric checks, `thickness_options_mm` block) are each unique enough that a helper would add overhead without net gain. No further `reuse`/`simplify`/`efficiency` opportunity identified.

`hasImprovement: false` → `dryStreak → 1`. Loop would stop on dry-streak 2 (or maxIterations cap, which was already reached).

---

## Full diff (HEAD of `improve-loop-validator-r1` vs base)

```diff
diff --git a/pilot-artifacts/validator.py b/pilot-artifacts/validator.py
index 5200e0e..b8c70c5 100644
--- a/pilot-artifacts/validator.py
+++ b/pilot-artifacts/validator.py
@@ -65,12 +65,25 @@ CERTIFICATIONS = {
     "ISO_14001", "recycled_content_certified",
 }
 
+NATURAL_STONES = {
+    "granite", "marble", "quartzite", "travertine", "limestone",
+    "onyx", "soapstone", "slate", "terrazzo",
+}
+
 REQUIRED_FIELDS = {
     "sku", "product_name", "material_type", "primary_color_family",
     "finish", "applications", "price_tier", "availability",
     "is_outdoor", "enrichment_confidence",
 }
 
+OPTIONAL_FIELDS = [
+    "manufacturer", "series_name", "secondary_color_family", "pattern_type",
+    "thickness_options_mm", "weather_rating", "uv_resistant", "heat_resistance",
+    "scratch_resistance", "stain_resistant", "sealing_required", "care_level",
+    "edge_profiles_available", "certifications", "recycled_content_pct",
+    "warranty_years", "country_of_origin",
+]
+
 SKU_PATTERN = re.compile(r"^[A-Z0-9\-]{3,30}$")
 COUNTRY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")
 
@@ -173,17 +186,11 @@ def validate(row: dict[str, Any]) -> dict[str, Any]:
     if row.get("care_level") is not None:
         _check_enum(row, "care_level", CARE_LEVELS, errors)
 
-    # applications — array of enums
+    # applications — array of enums (required, min 1 item)
     if isinstance(row.get("applications"), list):
         if len(row["applications"]) < 1:
             errors.append("min_items:applications must have at least 1 item")
-        seen = set()
-        for app in row["applications"]:
-            if app not in APPLICATIONS:
-                errors.append(f"enum:applications contains invalid value '{app}'")
-            if app in seen:
-                errors.append(f"unique:applications value '{app}' is duplicated")
-            seen.add(app)
+        _check_enum_array(row["applications"], "applications", APPLICATIONS, errors)
 
     # edge_profiles_available
     eps = row.get("edge_profiles_available")
@@ -191,13 +198,7 @@ def validate(row: dict[str, Any]) -> dict[str, Any]:
         if not isinstance(eps, list):
             errors.append("type:edge_profiles_available must be array or null")
         else:
-            seen_ep = set()
-            for ep in eps:
-                if ep not in EDGE_PROFILES:
-                    errors.append(f"enum:edge_profiles_available invalid value '{ep}'")
-                if ep in seen_ep:
-                    errors.append(f"unique:edge_profiles_available '{ep}' duplicated")
-                seen_ep.add(ep)
+            _check_enum_array(eps, "edge_profiles_available", EDGE_PROFILES, errors)
 
     # certifications
     certs = row.get("certifications")
@@ -205,13 +206,7 @@ def validate(row: dict[str, Any]) -> dict[str, Any]:
         if not isinstance(certs, list):
             errors.append("type:certifications must be array or null")
         else:
-            seen_cert = set()
-            for cert in certs:
-                if cert not in CERTIFICATIONS:
-                    errors.append(f"enum:certifications invalid value '{cert}'")
-                if cert in seen_cert:
-                    errors.append(f"unique:certifications '{cert}' duplicated")
-                seen_cert.add(cert)
+            _check_enum_array(certs, "certifications", CERTIFICATIONS, errors)
 
     # -- 6. Numeric range checks ----------------------------------------------
     conf = row["enrichment_confidence"]
@@ -275,11 +270,9 @@ def validate(row: dict[str, Any]) -> dict[str, Any]:
             )
 
     # sealing_required=false AND material is natural stone => flag (not hard error)
-    natural_stones = {"granite", "marble", "quartzite", "travertine", "limestone",
-                      "onyx", "soapstone", "slate", "terrazzo"}
     if (
         row.get("sealing_required") is False
-        and row.get("material_type") in natural_stones
+        and row.get("material_type") in NATURAL_STONES
     ):
         flags.append(
             "confidence:natural_stone with sealing_required=false is unusual — verify"
@@ -312,15 +305,8 @@ def validate(row: dict[str, Any]) -> dict[str, Any]:
         )
 
     # Many null optional fields on a non-coming_soon product => flag
-    optional_fields = [
-        "manufacturer", "series_name", "secondary_color_family", "pattern_type",
-        "thickness_options_mm", "weather_rating", "uv_resistant", "heat_resistance",
-        "scratch_resistance", "stain_resistant", "sealing_required", "care_level",
-        "edge_profiles_available", "certifications", "recycled_content_pct",
+        "warranty_years", "country_of_origin",
-    ]
     null_count = sum(
-        1 for f in optional_fields if row.get(f) is None
+        1 for f in OPTIONAL_FIELDS if row.get(f) is None
     )

(+_check_enum_array helper added in helpers section — see commit 030eab7)
```

---

## No-merge confirmation

Branch `improve-loop-validator-r1` was NOT merged and no PR was created. To review:

```bash
git log improve-loop-validator-r1 --oneline
# 5653ba0 refactor(validator): hoist optional_fields list to module-level OPTIONAL_FIELDS constant
# a9a5cc6 refactor(validator): hoist natural_stones set to module-level NATURAL_STONES constant
# 030eab7 refactor(validator): extract _check_enum_array helper to eliminate duplicated array-of-enum loop

git diff feature/SAG-4191-runtime-eval...improve-loop-validator-r1 -- pilot-artifacts/validator.py
```

Merge at your discretion after review.

---

## Execution note

The demo ran as a single extended agent (the Workflow PLAN sub-agent triggered the `improve-loop` skill in its context and then ran the loop manually). The designed multi-agent path (separate PLAN, CODE, REVIEW agents via the Workflow tool's `agent()` API) was not triggered in this demo execution. Key difference: the 3-lens adversarial REVIEW panel did not run — verification came from the CODE agent's own `verifyCmd` output (ALL PASS each round). 

The Workflow tool's multi-agent path is what the script template implements. To get the full 3-lens review, invoke via:

```js
Workflow({ scriptPath: 'skills-src/improve-loop/templates/improve-loop.workflow.js',
           args: { target: 'path/to/file', maxIterations: 3, dryStreakToStop: 2 } })
```
