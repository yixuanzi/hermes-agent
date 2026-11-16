# A2A poll activity heartbeat

## Scope

Keep the parent Agent active while `aegis_a2a_delegate` successfully polls a remote
task. This change does not alter remote task deadlines,
poll frequency, cancellation, or foreground-input behavior.

## Design

`_A2ADelegateSession` receives the parent Agent as an optional runtime
dependency. After each successful `get_task()` call in `_wait_for_final()`, it
best-effort calls `parent_agent._touch_activity()` with an A2A polling status.
The call happens only after the remote response is received, so failed polls do
not mask a stalled or broken connection. The task id is included only as a
bounded diagnostic identifier.

The heartbeat remains fail-open: a missing or failing activity hook must never
affect remote delegation. Existing output emission and terminal state handling
are unchanged.

## Verification

Add an async unit test with a fake A2A client that returns an in-progress task
then a completed task. Assert one parent activity touch follows the successful
poll and that the final task is returned.
