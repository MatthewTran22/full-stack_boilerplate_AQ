# Sandbox Cleanup Plan

## Problem
Daytona sandboxes are never deleted, causing storage to hit the 30GiB limit.

## Cleanup Triggers

| Trigger | Mechanism |
|---|---|
| User clicks "New" or logo | Frontend calls `DELETE /api/sandbox/{cloneId}` |
| User clicks a history item | Frontend calls `DELETE /api/sandbox/{cloneId}` before loading new clone |
| User closes tab (after generation) | `navigator.sendBeacon` fires `POST /api/sandbox/{cloneId}/end` |
| User closes tab (during generation) | SSE disconnect detected server-side → backend calls `cleanup_sandbox()` |

## Changes

### Backend (`backend/app/routes/clone.py`)
- Add `DELETE /api/sandbox/{clone_id}` — calls existing `cleanup_sandbox(clone_id)`
- Add `POST /api/sandbox/{clone_id}/end` — same logic, beacon-compatible (beacons are POST-only)
- On SSE disconnect (client drops connection mid-stream), call `cleanup_sandbox(clone_id)` in a finally block

### Frontend (`frontend/lib/api.ts`)
- Add `endSandbox(cloneId: string)` — sends DELETE to `/api/sandbox/{cloneId}`

### Frontend (`frontend/app/page.tsx`)
- Add `cloneId` to state (capture from SSE stream)
- Call `endSandbox(cloneId)` in `reset()` and `handleHistoryClick()`
- Add `beforeunload` event listener that fires `navigator.sendBeacon('/api/sandbox/{cloneId}/end')`
- Clean up the listener on unmount / when cloneId changes

## Existing Code
- `cleanup_sandbox()` in `sandbox.py:257` already handles Daytona deletion + in-memory cleanup
- Clone ID is emitted in the SSE stream but not currently stored in frontend state
