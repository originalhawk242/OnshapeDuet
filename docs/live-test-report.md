# Live test report (for a fix agent)

Branch `fix/live-microversion-key` (3 commits, 378 tests pass, not pushed). Live run used 47 of 50
requests on a throwaway document; budget is now 0. Details: `docs/live-checklist.md` ("Live results").

## Already fixed on the branch (verified live, regression tests added)
1. `currentmicroversion` returns `{"microversion": ...}` (code expected `microversionId`).
2. Variables are `assignVariable` features: name in string param `name`, value in quantity param
   `lengthValue`, feature name template `#name = #value`. Create and update both work.

## To act on
1. **Stale-write guard is client-side only (most important).** Onshape does NOT honor
   `rejectMicroversionSkew`: a stale-`sourceMicroversion` POST was applied and the reply had
   `microversionSkew: true`. The pre-POST `currentmicroversion` backstop (`tools.py` `_backstop`) is
   the only guard and must stay; a human edit between that GET and the POST still lands.
   - Correct comments/docstrings that imply server-side rejection (`backends/onshape.py` `_write`
     docstring, `tools.py` ~line 168, `backends/base.py`), and any wording that suggests the
     backstop is temporary ("may only be removed after ...", checklist step 1).
   - Make sure the user-facing reply for this case says the write DID likely land (currently
     `StaleVersion(may_have_landed=True)`); confirm the tools surface that clearly.
2. **Diameter dimension not visible in the UI.** Sketch is fully defined and Measure shows 8 mm,
   but the user did not see an "8" dimension. Check `sketch/serialize.py` `DIAMETER` (checklist
   item 12): is a driving diameter dimension actually written, or is size fixed some other way?
   Needs one live look at the sketch feature JSON (about 2 requests; the budget must be raised first).
3. **Variable update drops extra params.** After updating `plate_w`, the feature kept only
   `name` and `lengthValue` (the UI-created one also had `mode`, `variableType`, `value`, ...).
   Worked, but consider sending `mode`/`variableType` on update too.
4. **Variable create reply** carries no exact new version, so a re-read is demanded. The reply was
   not captured; if wanted, record it live and parse `sourceMicroversion` if present.
5. **Messages (minor):** with no credentials `cad_read` reports the missing credential before the
   budget hint; a non-Onshape host (`https://example.com/x`) gets no `base_url` hint. Either fix
   the wording or update `docs/testing-guide.md` Part B expectations.
6. **Untested live:** a feature name containing a real newline (the UI refuses it). Only the offline
   tests cover forged outline lines. A 256-char name rendered as one truncated line (fine).
7. Only LENGTH variables are supported; angle/number are not.

## Verified OK live
Sketch create (`dof 0`, status `OK`), `#plate_w` linking (width followed 40 -> 50 mm), client stale
refusal (1 request, nothing written), dry-run refusals (floating, arc, parallel+perpendicular),
request counts match the checklist.
