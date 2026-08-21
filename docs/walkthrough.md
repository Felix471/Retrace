# Demo walkthrough

1. Install the project from the repository root with `python -m pip install -e .`.

   **EXPECTED:** `retrace-logs --version` prints a Retrace version and the command is available.

2. Start the viewer with `retrace-logs view demo/`.

   **EXPECTED:** the browser opens on a batch table headed **40 runs**, with 40 rows across its pages, zero ingest warnings, and a populated **Failure modes** distribution. The terminal reports 40 runs and 523 events.

3. Tour the batch table. Click **Cost** to sort, choose `escalated` under **Outcome**, and then clear filters. Under **Group by**, choose `model_name`. The metadata keys available for filtering, grouping, and optional columns are `issue_area`, `model_name`, and `routing_variant`.

   **EXPECTED:** cost sort changes direction on a second click; the outcome filter leaves 13 escalated runs; grouping shows `support-small-v2` with 14 runs, `support-medium-v2` with 13, and `support-large-v2` with 13.

4. Open `support-demo-03` in replay. Set **Agent** to `reviewer`, **Phase** to `quality_review`, or **Type** to `tool_call`, then clear those selections and search for `synthetic`.

   **EXPECTED:** each selector narrows the timeline and its loaded/matching count; search highlights matching text without changing the server-side total. The existing tag is MAST mode `1.3` and is anchored to `support-demo-03:0`.

5. Select an event in `support-demo-03`, leave **Anchor to selected events** checked, choose mode `2.3`, enter an optional note, and click **Add tag**.

   **EXPECTED:** the Tags count increases from 1 to 2 and the new marker appears on the selected event. The deterministic starter tag and your new tag are stored together in `demo/support-demo-03/retrace.json`.

6. Return to the run table.

   **EXPECTED:** the Failure modes chart now reports 6 total tags instead of 5, and mode `2.3` has one tag. Before this manual edit, the committed distribution has five tagged runs and one tag in each mode `1.3`, `2.2`, `2.6`, `3.1`, and `3.2`.

7. Clear filters, select exactly `support-demo-05` and `support-demo-06`, and click **Compare**.

   **EXPECTED:** the banner says **Runs diverge structurally at pair 6** because `support-demo-06` has an extra retry loop. For a comparison with both kinds, compare `support-demo-01` with `support-demo-02`; the headline says **Runs diverge structurally at pair 13** and the secondary line says **first content difference at pair 0**.

## Second step: inspect aggregate JSONL and repairs

This is the author's real experiment corpus, included because it contains real logging defects the tool repairs and flags; the game domain is irrelevant.
After completing the demo tour, run `retrace-logs view fixtures/avalon_mini`.
This fixture demonstrates the LINE-PER-RUN layout: five runs come from one
aggregate JSONL file, and each run id is the per-line `gameId` value. The viewer
shows 5 runs and 652 events.

It also makes repairs visible. In game 8, the run whose id ends in `kwy8o` shows
the banner **1 records repaired in this run**. Its repaired event has a
**repaired** badge; expand it to see the original values under **Provenance**:
turn 4 is shown as 5, and result `fail` is shown as `success`. The run whose id
ends in `6ius8` shows **2 records repaired in this run**.

This is the author's real experiment corpus, included because it contains real logging defects the tool repairs and flags; the game domain is irrelevant.
Run `retrace-logs check fixtures/avalon_mini` to see the repair-rule fire
counts: `ordinal` fired 3 and `derive` fired 1.
