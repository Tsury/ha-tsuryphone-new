# TsuryPhone Home Assistant Integration (New) – Design & Progress Tracker

> Status: INITIAL DRAFT (v0.1)
> Target Minimum HA Core: **2025.9.0**
> Domain: `tsuryphone`

---
## 0. Purpose
A clean, modern Home Assistant (HA) integration for the TsuryPhone device, replacing all legacy projects. This document is the authoritative source for:
- Functional & non-functional requirements
- Architecture & data contracts
- Implementation phases & progress tracking (checkboxes)
- Risks, decisions, change log
- Test strategy & validation gates

It will be iteratively updated as implementation proceeds. Progress sections use ✅ (done), ⏳ (in progress), ⭕ (pending), ❗ (blocked), 🔄 (revisit), 💤 (deferred), 🚧 (scoped but not started).

---
## 1. High-Level Goals
| Goal | Description | Success Metric |
|------|-------------|----------------|
| G1 | Fully featured HA integration using latest APIs | All required entities/services functional |
| G2 | Real-time state via WebSocket + fallback polling | <2s lag typical, seamless reconnect |
| G3 | Full event emission for automations | All categories mapped & documented |
| G4 | Hybrid list management (quick dial / blocked) | Lists editable via Options + services |
| G5 | Webhook action dual provisioning (manual / HA-managed) | Both workflows succeed & sync to device |
| G6 | Robust reboot & seq handling | No stale state after device restart |
| G7 | Local real-time call duration | 1s updates until call end |
| G8 | Rich diagnostics & developer observability | Diagnostics export + internal counters |
| G9 | Test coverage for core paths | ≥ 85% lines for logic modules |
| G10 | Clean uninstall & persistence | No orphaned device config; state restored on restart |

---
## 2. Scope Overview
### In Scope
- Zeroconf discovery (mDNS / http service, TXT `device=tsuryphone`)
- Config Flow + Options Flow (all configuration categories)
- Entities (core phone, stats, audio, mode, pattern select, quick dial select, call status, etc.)
- Services (§8)
- Device triggers & conditions
- Event bus propagation of all device events (separate event types)
- Sequence enforcement & reboot detection
- Webhook action management (manual + HA-created)
- Real-time call duration ticker (local)
- Persistent cache (restore state on HA reboot)
- Diagnostics output
- Import/export for quick dials & block list

### Out of Scope (Initial)
- TLS / HTTPS to device
- Authentication / tokens
- Gzip compression
- Large-scale phonebook sync
- Webhook invocation acknowledgment events (device does not emit yet)

### Deferred / Future Hooks
- Security hardening (auth layer)
- Per-webhook HA triggers on invocation confirmations
- Multi-protocol bridging (Android integration reuse)

---
## 3. Requirements Traceability Matrix
| ID | Requirement | Source Decision | Impl Artifact(s) | Status |
|----|-------------|-----------------|------------------|--------|
| R1 | Auto-discover device via mDNS on port 8080 | Answers 2.x | `config_flow.py` | ⭕ |
| R2 | Unique ID = deviceId (MAC-based, immutable) | Answers 3.x | `config_flow.py` | ⭕ |
| R3 | WebSocket primary, retry w/ backoff | 4.x | `websocket.py` | ⭕ |
| R4 | Poll fallback (30s) when WS down | 4.2 / 16.1 | `coordinator.py` | ⭕ |
| R5 | Log schemaVersion mismatch (warn only) | 6.1 | `websocket.py` | ⭕ |
| R6 | Drop events seq <= last_seq | 17.1 | `websocket.py` | ⭕ |
| R7 | Reboot detection triggers snapshot refetch | 6.4 / 15.2 | `coordinator.py` | ⭕ |
| R8 | All events dispatched on HA bus | 7.1 | `events dispatcher` | ⭕ |
| R9 | Separate event types per category/subtype | 7.2 | `events dispatcher` | ⭕ |
| R10 | Real-time call duration (1s) | 7.3 | `call_timer task` | ⭕ |
| R11 | Config delta keys surfaced individually | 7.4 | `config_delta handler` | ⭕ |
| R12 | Event history buffer (300, evict oldest) | 7.5 / 24.1 | `history ring buffer` | ⭕ |
| R13 | Core binary sensors (ringing, dnd, maintenance, in_call, call_waiting_available) | 8.1 / 14.x | `entity_binary_sensors.py` | ⭕ |
| R14 | Stats & call state sensors | 8.2 / 8.3 | `entity_sensors.py` | ⭕ |
| R15 | Force & maintenance switches | 8.4 | `entity_switches.py` | ⭕ |
| R16 | Buttons (answer, hangup, ring, reset, refetch, refresh, dial_selected, toggle_call_waiting) | 8.5 / 8.10 | `entity_buttons.py` | ⭕ |
| R17 | Audio config numbers (range 1–7) | 8.6 | `entity_numbers.py` | ⭕ |
| R18 | Ring pattern select with presets + custom | 8.7 | `entity_selects.py` | ⭕ |
| R19 | Hybrid quick dial model + select | 8.8 | `select + services` | ⭕ |
| R20 | Hide webhook actions from entities | 8.9 | (No entity) | ⭕ |
| R21 | Call waiting available sensor | 8.10 / 14.1 | `entity_binary_sensors.py` | ⭕ |
| R22 | Services complete list implemented | 9.x | `services.py` | ⭕ |
| R23 | Partial update semantics for audio & DND | 9.3 / 12.x | `api_client.py` | ⭕ |
| R24 | Quick dial & blocked import/export | 9.5 / 8.8 hybrid | `services.py` | ⭕ |
| R25 | Snapshot + refetch services | 9.6 | `services.py` | ⭕ |
| R26 | Synthetic snapshot service event | 9.7 | `services.py` | ⭕ |
| R27 | Options Flow includes all configuration groups | 10.1 | `config_flow.py` | ⭕ |
| R28 | Structured list management UI (no pagination) | 10.4 | `options flow` | ⭕ |
| R29 | Dual webhook provisioning workflow | 11.1 | `services.py` + webhook util | ⭕ |
| R30 | Stale webhook detection | 11.2 | `coordinator.py` | ⭕ |
| R31 | Provide device triggers & conditions | 21.x | `triggers.py` / `conditions.py` | ⭕ |
| R32 | Event queue cap & overflow warning | 24.1 | `websocket.py` | ⭕ |
| R33 | Config delta only for config (rare) | 24.2 | (Implicit) | ⭕ |
| R34 | Fallback poll interval 30s, refetch 5m | 34.1 | `coordinator.py` | ⭕ |
| R35 | Persist state cache | 18.1 | `storage_cache.py` | ⭕ |
| R36 | Raw numbers (no masking) | 29.x | (Policy) | ⭕ |
| R37 | Device removal optional cleanup (URL/webhooks) | 30.1 | `__init__.py` unload | ⭕ |
| R38 | Device disable keeps config | 30.2 | HA standard | ⭕ |
| R39 | Error code mapping / translations | 31.x | `const.py` + translations | ⭕ |
| R40 | Diagnostics bundle | 19.1 | `diagnostics.py` | ⭕ |
| R41 | Include webhook IDs & lists in diagnostics | 19.2 | `diagnostics.py` | ⭕ |
| R42 | Action codes dump debug service | 19.3 | `services.py` | ⭕ |
| R43 | Logging default DEBUG initial, configurable | 20.x | `const.py` + options | ⭕ |
| R44 | Persistent notifications (maintenance, error, reboot) | 13.1 / 15.x | `notifications helper` | ⭕ |
| R45 | Synthesize call start if only end received | 27.2 | `event logic` | ⭕ |
| R46 | Leave stale call if device reboots mid-call | 27.1 | `logic doc` | ⭕ |
| R47 | Accept empty pattern -> default ring | 25.3 | `api_client.py` | ⭕ |
| R48 | Rate limit service concurrency | 24.1 | `api_client.py` | ⭕ |
| R49 | Sequence-based snapshot refresh on reboot | 6.4 / 15.2 | `coordinator.py` | ⭕ |
| R50 | Test harness + mock server | 28.1 | `tests/fixtures` | ⭕ |
| R51 | Persistent call history (incoming/outgoing/blocked/missed) | New user request | `call_history.py` + storage | ⭕ |
| R52 | Missed call detection (incoming->idle without InCall) | New user request | `coordinator.py` | ⭕ |
| R53 | History exposed (sensor size + diagnostics + export service) | New user request | `entity_sensors.py` / `services.py` | ⭕ |
| R54 | Services: call_history_export / call_history_clear | New user request | `services.py` | ⭕ |
| R55 | Logbook entries for finalized calls | Suggestion | `__init__.py` | ⭕ |
| R56 | Future keypad streaming digits (design placeholder) | Future | `FUTURE keypad spec` | 💤 |
| R57 | Keypad API contract placeholder (digit/backspace/commit) | Future | `FUTURE keypad spec` | 💤 |
| R58 | Missed call automation trigger | Extension | `triggers.py` | ⭕ |
| R59 | History capacity cap & pruning (default 500) | Design | `call_history.py` | ⭕ |
| R60 | Optional masking toggle in history export (deferred) | Privacy future | `diagnostics.py` | 💤 |
| R61 | Device surfaces call waiting availability flag (callWaitingId exposure) | Firmware debt | Firmware update | 💤 |

*(Matrix will be updated with statuses.)*

---
## 4. Data Contracts (Inbound Events)
Reference: Device JSON schemaVersion=2.

### 4.1 Root Event Fields
```
schemaVersion: int (const 2)
seq: int (monotonic per boot)
ts: int (device ms epoch / uptime? – treat as monotonically increasing per boot)
integration: "ha" (device configured) or other
category: string (call | phone_state | system | config | diagnostic)
event: subtype string
```

### 4.2 Event Subtypes & Handling Table
| Category | Event | Payload Extras | HA Event Name | Notes |
|----------|-------|----------------|---------------|-------|
| call | start | number, isIncoming, callStartTs | tsuryphone_call_start | Start duration timer |
| call | end | number, isIncoming, callStartTs, durationMs? | tsuryphone_call_end | Stop timer; fallback local duration |
| call | blocked | number, isIncoming=true | tsuryphone_call_blocked | Increment blocked count |
| phone_state | state | state,int previousState,int ... | tsuryphone_phone_state_state | Update app + derived |
| phone_state | dialing | currentDialingNumber | tsuryphone_phone_state_dialing | Update dialing sensor |
| phone_state | ring | isRinging | tsuryphone_phone_state_ring | Ringing binary sensor |
| phone_state | dnd | dndActive | tsuryphone_phone_state_dnd | DND toggle |
| phone_state | call_info | currentCallNumber,... | tsuryphone_phone_state_call_info | Supplementary call snapshot |
| system | stats | stats.calls.totals... | tsuryphone_system_stats | Merge stats |
| system | status | freeHeap,rssi,uptime | tsuryphone_system_status | Sys metrics |
| system | error | error | tsuryphone_system_error | Create notification |
| system | shutdown | reason | tsuryphone_system_shutdown | Pre-restart marker |
| config | config_delta | key/newValue(/oldValue) or changes[] | tsuryphone_config_delta | Per-key dispatch |
| diagnostic | snapshot | composite | tsuryphone_diagnostic_snapshot | Full snapshot |

---
## 5. Internal State Machine
- `seq_last`: highest accepted seq
- `reboot_detected`: bool when seq regression or reset pattern
- `call_active`: derived from app_state == InCall OR active callStartTs present
- Timer task only spawns if call_active transitions false->true and not already running

---
## 6. Ring Pattern Presets
```
DEFAULT (device native)
pulse_short = "300,300x2"
classic = "500,500"
long_gap = "800,400"
triple = "300,300,300"
stagger = "500,250,500"
alarm = "200,200x5"
slow = "1000"
burst = "150,150x3"
custom = <user provided pattern>
```

---
## 7. Entity Model Mapping
(States/attributes summary)

### 7.1 Binary Sensors
| Name | Attrs | Source |
|------|-------|--------|
| ringing | none | ring events |
| dnd | none | dnd events / state field |
| maintenance_mode | none | phone_state state + config updates |
| in_call | callStartTs + state logic | state events |
| call_waiting_available | heuristic (presence callWaitingId in future) | TODO logic |

### 7.2 Sensors
- App state (`stateName`)
- Current call number
- Current dialing number
- Real-time call duration (sec)
- Last call number/type
- Uptime (seconds)
- RSSI (dBm)
- Free heap (bytes)
- Calls total/in/out/blocked/talkTimeSeconds
- quick_dial_count (len)
- blocked_count
- last_blocked_number

### 7.3 Switches
- force DND (maps to DND config `force`)
- maintenance_mode

### 7.4 Numbers
- Audio: earpieceVolume, earpieceGain, speakerVolume, speakerGain (1–7)

### 7.5 Selects
- ring_pattern (preset or custom sentinel)
- quick_dial (options = code or name with code fallback)

### 7.6 Buttons
(See R16 + dial_quick_dial via selected option)

---
## 8. Services (Detailed Spec)
| Service | Params | Action | Error Modes |
|---------|--------|--------|-------------|
| dial | number | POST /api/call/dial | invalid number, phone not ready |
| answer | — | POST /api/call/answer | no incoming call |
| hangup | — | POST /api/call/hangup | no active call |
| ring | pattern? | pattern optional (empty=default) | invalid pattern |
| switch_call_waiting | — | toggle | not available |
| set_dnd | force?, scheduled?, start_hour?, start_minute?, end_hour?, end_minute? | partial update | invalid range |
| set_audio | any subset of 4 fields | partial update | validation range errors |
| set_ring_pattern | pattern | update ring pattern | invalid pattern |
| quick_dial_add | code, number, name? | add | conflict/invalid |
| quick_dial_remove | code | remove | not found |
| quick_dial_import | list(json) | batch add | partial failures aggregated |
| quick_dial_export | — | return list | — |
| blocked_add | number, reason? | add | duplicate |
| blocked_remove | number | remove | not found |
| blocked_import | list(json) | batch | partial failures |
| blocked_export | — | list | — |
| webhook_add | code, id?, action_name?, auto_create? | add (with optional HA webhook creation) | conflict |
| webhook_remove | code | remove | not found |
| set_ha_url | url | update HA URL | invalid URL |
| refetch_all | — | device reload + stats | device error |
| refresh_snapshot | — | ask device to broadcast snapshot | device error |
| diagnostic_snapshot | — | synth local snapshot event | — |
| dial_quick_dial | code | direct mapping | not found |
| dial_selected_quick_dial | — | uses selected option | not selected |
| action_codes_dump | — | log action codes (debug) | none |
| call_history_export | mask_numbers? (bool, default false) | Return structured list | — |
| call_history_clear | older_than_days? keep_last? | Prune history | — |

Return payloads follow unified success/error contracting to align with device responses.

---
## 9. Options Flow Structure
Sections & forms with grouping; dynamic sub-form for lists with add/remove per entry (non-paginated). Quick dial & blocked lists bulk import/export JSON text area.

---
## 10. Persistent Cache
Fields persisted: app_state, ringing, dnd, maintenance, last call, active call (if any), callStartTs (note: will NOT resume timer; call duration sensor resets on HA reboot), stats, config lists, ring pattern, selected quick dial, audio config.

On reload: mark attribute `restored: true` until first live event.

---
## 11. Reboot & Snapshot Logic
Algorithm:
1. On event with seq < last_seq OR seq small after large prior → `reboot_detected=true`.
2. Cancel call duration timer.
3. Force `refetch_all` (rate-limited: not more than once per 10s).
4. Clear transient dialing number.

---
### 11.1 Call History Model
Rolling buffer (fixed max 500 entries for v1; future option if needed). Entry schema (newest-first ordering on export):
```
{
	"ts_device": int,
	"received_ts": float,
	"seq": int,
	"type": "incoming"|"outgoing"|"blocked"|"missed",
	"number": string,
	"duration_s": int|null,
	"is_incoming": bool,
	"reason": string|null
}
```
Derivation rules:
1. call.start → provisional incoming/outgoing record (no duration yet).
2. call.end → finalize matching provisional (fill duration_s using device durationMs/1000 fallback local).
3. call.blocked → immediate finalized blocked record.
4. missed → any incoming ringing sequence that returns to Idle without an InCall transition (no minimum ring duration filter).
Pruning: drop oldest on overflow (log every 100 drops). Exposure: size sensor, diagnostics (<=200 recent + total), export service. Clear service supports conditional pruning.

### 11.2 Logbook Integration
On finalize push human-readable entry (Incoming/Outgoing/Blocked/Missed). Duration formatted hh:mm:ss if available.

### 11.3 Future: Interactive Keypad Streaming (Placeholder)
Proposed device commands (not yet implemented device-side):
| Command | Payload | Purpose |
|---------|---------|---------|
| dial_digit | {"digit":"0-9"} | Append digit; server returns classification & buffer |
| dial_backspace | {} | Remove last digit |
| dial_clear | {} | Clear buffer |
| dial_commit | {} | Commit call (maps to existing dial) |
| dial_predict | {} | Return classification without mutation (optional) |
Response suggestion:
```
{
	"schemaVersion":2,
	"success":true,
	"data":{
		 "buffer":"123",
		 "isComplete":false,
		 "isValid":true,
		 "classification":"partial",  // quick_dial|direct|webhook|partial|invalid
		 "targetNumber":"..."         // if resolvable
	}
}
```
HA will later expose a keypad card using websocket for low latency; fallback HTTP. Requirements R56/R57 remain 💤 until firmware support.

---
### 11.4 Clarified Behavioral Decisions
| Aspect | Decision |
|--------|----------|
| Missed call rule | Any ring without InCall → missed (no duration threshold) |
| History export order | Newest first |
| History clear precedence | Apply older_than filter then enforce keep_last cap |
| Webhook stale detection | Performed at startup and on Options open |
| Quick dial option label | "Name (code)" if name present else code |
| Ring pattern custom sentinel | Selecting custom retains previous pattern until user submits new one |
| Reboot detection | Any seq decrease triggers reboot workflow |
| Missed call HA event name | tsuryphone_call_missed |
| Event history buffer | Includes all event types (config_delta included) |
| Diagnostics history slice | 200 most recent entries (with total count) |
| Initial debug window | 300s before auto-downgrade unless verbose option set |
| Webhook auto-create naming | "TsuryPhone <code> Action" |
| Call waiting availability | Assume future firmware will expose; heuristic interim; tracked as R61 |
| Call history size configurability | Fixed 500 (v1) |

---
## 25. Firmware Debt / External Dependencies
| Item | Description | Impact if Missing | Status |
|------|-------------|-------------------|--------|
| Call waiting flag exposure | Device to surface callWaitingId / availability in phone_state events | Sensor may be heuristic only | Pending (R61) |
| Keypad streaming command set | Low-latency digit-by-digit commands & responses | Keypad feature postponed | Future (R56/R57) |

---
## 26. Out-of-Scope Suggestions (Deferred)
Previously proposed feature suggestions A–J explicitly deferred for v1; reinstate only via new requirement IDs if prioritized later.

---
## 12. Call Duration Timer
- Start at call_start event: `call_local_start_monotonic = time.monotonic()`.
- Update each second (Coordinator-managed task).
- If call_end arrives: commit final (prefer device durationMs else computed) then stop.
- If no end after 8h: auto-stop (safety watchdog).

---
## 13. Error Handling & Notifications
| Event/Error | User Visible Action |
|-------------|---------------------|
| system_error | Persistent notification + HA log error |
| maintenance_mode true | Persistent notification |
| shutdown (reason=reset_requested) | Log info; expect reconnect |
| queue overflow | Single warning per minute |
| schema mismatch | Warning once per boot |

---
## 14. Diagnostics Contents
- Device meta (id, host, port, version, last_seq, reboot_detected)
- Last 50 events (raw JSON)
- Stats & system metrics
- Config lists (quick dials, blocked, webhooks)
- Audio & DND config
- Queue sizes (event queue length, dropped count)
- Preset mapping summary
- Webhook stale entries list

---
## 15. Logging Policy
- Default DEBUG for first 300s or until stable state (5 consecutive successful WS pings or events) then downgrade INFO unless option `verbose_events` is set.
- Structured debug lines prefixed `[tsuryphone]` for easy filtering.

---
## 16. Test Plan (Key Cases)
| Area | Tests |
|------|-------|
| Discovery | Zeroconf create entry, duplicate prevention |
| Config Flow | Manual host fallback, schema warning displayed |
| WebSocket | Normal stream, reconnect, seq regression, overflow handling |
| Events | Each category updates correct entities & fires HA events |
| Call Lifecycle | start/end, missing start (synth), timer increments |
| Config Delta | Single + aggregated multi changes apply in order |
| Services | All success + error scenarios (invalid inputs) |
| Lists | Quick dial import/export round-trip; blocked list operations |
| Webhooks | Manual add, auto-create simulation (mock webhook registry) |
| Reboot | Snapshot refetch triggered, stale call cleared |
| Cache | Restore state on restart (sensor states present before WS connect) |
| Diagnostics | Output schema & includes required sections |
| Options Flow | Changing ring pattern (preset/custom), audio partial update |

Coverage Tools: pytest + pytest-asyncio + anyio; static: mypy; style: ruff.

---
## 17. Implementation Phases & Progress
| Phase | Focus | Key Deliverables | Status |
|-------|-------|------------------|--------|
| P1 | Skeleton + discovery + manifest | manifest, const, config_flow basic, api skeleton | 🚧 |
| P2 | WS + event dispatcher + core entities | websocket.py, coordinator, basic sensors | ⭕ |
| P3 | All entities + services | entity modules, services.py | ⭕ |
| P4 | Lists & webhooks hybrid model | select + import/export + webhook mgmt | ⭕ |
| P5 | Triggers, conditions, notifications | triggers.py, conditions.py, notifications | ⭕ |
| P6 | Options Flow full | advanced forms & validations | ⭕ |
| P7 | Diagnostics + cache | storage_cache.py + diagnostics.py | ⭕ |
| P8 | Resilience polish | reboot, sequence, overflow tests | ⭕ |
| P9 | Tests & CI scaffolding | tests/, workflows (optional) | ⭕ |
| P10 | Docs & translations | README, en.json | ⭕ |
| P11 | Final QA & cleanup | Lint, type check, risk review | ⭕ |

---
## 18. Risks & Mitigations
| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Ambiguous call_waiting availability | Wrong sensor state | M | Add heuristic & allow refinement later |
| Device reboot mid-call | Stale call duration | M | Policy: leave stale (document) |
| Event burst causing queue overflow | Dropped events | L-M | Cap & log; snapshot refetch on reboot detection |
| Partial failures on bulk import | Inconsistent lists | M | Collect results & emit summary event |
| Future schema change | Break parsing | M | Version warning + defensive parsing |

---
## 19. Open Questions (Currently None)
All previously asked questions resolved. Any new requirements will be appended here.

---
## 20. Change Log
| Version | Date | Author | Change |
|---------|------|--------|--------|
| 0.1 | 2025-09-06 | draft | Initial comprehensive design & planning | 

---
## 21. Next Immediate Steps
1. Create integration directory structure & manifest (P1)
2. Implement constants + ring pattern presets
3. Stub `api_client.py` + `websocket.py` with connection logic placeholders
4. Basic config_flow: manual + zeroconf discovery path
5. Commit & update this doc (mark P1 tasks ⏳)

---
## 22. Acceptance Criteria Summary
- All R# items status to ✅ except deferred/future before declaring v1.0.
- No unhandled exceptions in logs during 24h soak (test harness simulation).
- Reconnect & snapshot logic validated across at least 3 forced restarts.
- Service contract docs match actual runtime behaviours (tested).

---
## 23. Deviation Handling
If an implementation constraint forces a change, update:
1. Requirement row (status -> 🔄) with rationale
2. Risk table (if new risk introduced)
3. Change log (new version entry)

---
## 24. Appendix: Internal Module Responsibilities
| Module | Responsibility |
|--------|----------------|
| `__init__.py` | Entry setup, unload, reload orchestration |
| `manifest.json` | Metadata & version gating |
| `const.py` | Constants, enums, error maps, defaults |
| `config_flow.py` | Discovery & options flows |
| `api_client.py` | HTTP commands, error normalization, rate limiting |
| `websocket.py` | Event stream mgmt, backoff, queueing |
| `coordinator.py` | Central state aggregation, polling fallback, timers |
| `model.py` | Dataclasses / typed state containers |
| `storage_cache.py` | Persistent state caching layer |
| `entity_*` modules | Entity platform implementations |
| `services.py` | Registration & handlers for HA services |
| `triggers.py` | Device trigger definitions |
| `conditions.py` | Condition helpers |
| `diagnostics.py` | Diagnostics handler & packaging |
| `util.py` | Shared helpers (seq logic, throttling) |

---
_End of document – maintained through development._
