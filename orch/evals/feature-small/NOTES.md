Why: a realistic one-PR feature in one subsystem (summary module only, ~100-150 lines) must
run the whole flow: `orch:builder` spawned by the main session, then two `orch:reviewer`
spawns on Fable with `mode: review`, then one with `mode: crosscheck`
(`builder-review-crosscheck`, a regex on the trace that requires each spawn on a record with
`"parent_tool_use_id":null`, i.e. made by the main session). A single `p95` field (~10 lines)
was too small: the main thread edited it in place.
