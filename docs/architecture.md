# MotionLab v0.1 architecture

## Scope

The first increment proves a constrained Blender execution path. It does not implement the Grey UI, an OYOSKY reasoning engine, a general-purpose backend, rendering, animation generation, or GrowthFlow integration.

## Runtime flow

1. Grey or another authorized client requests work through the MotionLab service.
2. The service authenticates the Blender add-on and offers only approved jobs.
3. The add-on makes outbound HTTPS requests. No inbound laptop port is opened.
4. A network worker validates and queues the received job. It must not access Blender data.
5. A Blender application timer drains at most ten jobs per tick on Blender's main thread.
6. The local protocol and permission checks run again before an allowlisted operation executes.
7. The result is placed on the worker's outbound result queue and returned to the service.

## First command

Only `create_cube` is implemented. Arbitrary Python, shell commands, file access, scripts, rendering and destructive scene operations are not accepted.

Each job must contain an id, command, args and authorization metadata. The local gate requires `approved: true` and the `scene.write` scope. The service must independently enforce user approval and authorization; client-side checks are defense in depth, not a replacement for server authorization.

## Service contract expected by the connector

- `GET /v1/jobs/next` returns `{"job": null}` when no job is available, or `{"job": { ...validated job... }}`.
- A returned job must be leased to the authenticated add-on installation until its result is accepted or the lease expires. A job not accepted into the local queue must be made available again after lease expiry.
- The service must not dispatch another job to the same installation while an earlier job is leased, unless it explicitly supports bounded concurrent leases.
- `POST /v1/jobs/{job_id}/result` accepts a JSON execution result and is idempotent for a given job id. Repeated submissions must not execute or apply the job again.
- Both endpoints require a bearer token over HTTPS.
- The service must bind jobs to the authenticated add-on installation and prevent cross-user delivery and replay.
- The service must issue short-lived, revocable credentials. The add-on stores the token in Blender preferences; do not commit tokens or place them in source code.

The service is not implemented in this repository increment. The connector is a client-side contract implementation and cannot connect successfully until a compatible service exists.

## Threading and safety

- The network worker performs HTTP and queue operations only.
- The local command queue is bounded to 100 pending jobs and never blocks the network worker.
- The outbound result queue is also bounded to 100 entries. When it is full or a result submission fails, the add-on stops accepting further scene work until result delivery recovers.
- HTTPS request timeouts default to two seconds so add-on shutdown has a bounded wait. Shutdown does not attempt unlimited result retries; unacknowledged jobs remain subject to the service lease/recovery policy.
- Blender's main-thread timer processes at most ten jobs per tick to keep the UI responsive under load.
- All `bpy` calls run from Blender's main-thread timer callback.
- TLS certificate verification remains enabled by the standard HTTPS client.
- Only HTTPS service URLs are accepted; authenticated redirects are rejected.
- Responses are size-limited and parsed as JSON.
- Commands are allowlisted and validated before execution.
- Approval and scope are checked again immediately before execution.

## LogicalSky boundaries

MotionLab is a controlled execution environment. It does not duplicate Grey's orchestration or OYOSKY's reasoning. Any future OYOSKY adapter must preserve the existing read-only Grey-to-OYOSKY boundary and request execution through explicit MotionLab authorization.
