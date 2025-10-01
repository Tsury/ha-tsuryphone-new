# TsuryPhone Home Assistant Integration

Local integration for the TsuryPhone firmware: real‑time phone state, call control, configuration management, events, and automation support.

## Features

- Auto / manual device setup (mDNS `_http._tcp` and device ID)
- Real‑time WebSocket updates with fallback polling
- Call control: dial, answer, hang up, call waiting toggle
- Ring device (custom pattern optional)
- Quick dial management (add / remove / dial)
- Block / unblock numbers, priority caller management
- Do Not Disturb configuration (force / schedule)
- Audio configuration (earpiece & speaker volume + mic gain)
- Ring pattern set & presets
- Maintenance mode toggle
- Handset hook status sensor for automations
- Webhook action codes (dial short code triggers HA automations)
- Call history (cached locally for retention)
- Missed/blocked call notifications (optional)
- Diagnostics & resilience health checks

## Installation

1. Copy `custom_components/tsuryphone` into your Home Assistant `custom_components` folder (HACS packaging to follow if not already present).
2. Restart Home Assistant.
3. Add integration via UI: Settings → Devices & Services → Add Integration → "TsuryPhone".
4. Enter host (or accept discovered device) and optional port (default 8080).

## Configuration Flow

During setup:

1. Connection test performed (fetches device ID & versions).
2. Entities created after initial WebSocket connect or fallback poll.

Adjustable later via the integration Options (retention, verbosity, etc.).

## Entities (Overview)

| Category          | Examples                                                                                                                                   |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Binary Sensors    | Ringing, In Call, Handset Off Hook, Do Not Disturb, Maintenance Mode, Call Waiting Available                                               |
| Sensors           | Phone State, Current Call Number, Call Duration, Last Call Number, Signal Strength, Memory, Call Counters, Quick Dial Count, Blocked Count |
| Buttons           | Answer, Hang Up, Ring Device, Reset Device, Refresh Data, Toggle Call Waiting                                                              |
| Numbers / Selects | Audio levels, ring pattern selection (when exposed)                                                                                        |
| Switch / Others   | (Subject to future expansion)                                                                                                              |

Less frequently needed / diagnostic entities default to disabled in the registry—enable only if required.

## Core Services (Common)

| Service                            | Purpose                         | Key Fields                                              |
| ---------------------------------- | ------------------------------- | ------------------------------------------------------- |
| `tsuryphone.dial`                  | Dial a number                   | `device_id`, `number`                                   |
| `tsuryphone.answer`                | Answer ringing call             | `device_id`                                             |
| `tsuryphone.hangup`                | Hang up active call             | `device_id`                                             |
| `tsuryphone.ring_device`           | Ring locally (optional pattern) | `device_id`, `pattern?`                                 |
| `tsuryphone.set_dnd`               | Update DND config               | `device_id`, fields (force, scheduled, start/end times) |
| `tsuryphone.set_audio`             | Adjust audio levels             | `device_id`, volume/gain fields                         |
| `tsuryphone.set_ring_pattern`      | Set custom pattern              | `device_id`, `pattern`                                  |
| `tsuryphone.quick_dial_add/remove` | Manage quick dial entry         | `device_id`, `code`, `number`, `name?`                  |
| `tsuryphone.blocked_add/remove`    | Manage blocked number           | `device_id`, `number`, `reason?`                        |
| `tsuryphone.priority_add/remove`   | Manage priority caller          | `device_id`, `number`                                   |
| `tsuryphone.set_maintenance_mode`  | Toggle maintenance mode         | `device_id`, `enabled`                                  |
| `tsuryphone.dial_quick_dial`       | Dial quick dial code            | `device_id`, `code`                                     |
| `tsuryphone.refetch_all`           | Force device full refetch       | `device_id`                                             |
| `tsuryphone.reset_device`          | Reboot device                   | `device_id`                                             |

Additional advanced / diagnostic services are listed in the appendix.

## Events

| Event                            | Description                             |
| -------------------------------- | --------------------------------------- |
| `tsuryphone_call_start`          | Call began (incoming/outgoing + number) |
| `tsuryphone_call_end`            | Call ended (duration)                   |
| `tsuryphone_call_blocked`        | Blocked call number                     |
| `tsuryphone_phone_state_state`   | State transition                        |
| `tsuryphone_phone_state_dialing` | Dialing progress                        |
| `tsuryphone_phone_state_ring`    | Ring state change                       |
| `tsuryphone_phone_state_dnd`     | DND active flag                         |
| `tsuryphone_config_delta`        | Incremental config update               |
| `tsuryphone_system_stats`        | Periodic stats snapshot                 |
| `tsuryphone_system_status`       | Device status update                    |
| `tsuryphone_diagnostic_snapshot` | Diagnostic snapshot                     |
| `tsuryphone_webhook_action`      | Webhook action triggered                |

## Automations Examples

Example (notify on blocked call):

```yaml
automation:
	- alias: TsuryPhone Blocked Call Notification
		trigger:
			- platform: event
				event_type: tsuryphone_call_blocked
		action:
			- service: persistent_notification.create
				data:
					title: "Blocked Call"
					message: "Blocked call from {{ trigger.event.data.number }}"
```

Example (flash light on priority call):

```yaml
automation:
	- alias: TsuryPhone Priority Call Light
		trigger:
			- platform: state
				entity_id: binary_sensor.tsuryphone_priority_call_active
				to: 'on'
		action:
			- service: light.turn_on
				target: {entity_id: light.hallway}
				data: {flash: short}
```

## Call History & Storage

- Call history retained in local storage (size & retention configurable in future options).
- Missed calls tracked (service available to query).

## Resilience & Health

- WebSocket watchdog + exponential backoff
- Sequence validation to detect missed or out‑of‑order events
- Fallback periodic polling
- Manual `websocket_reconnect` and `run_health_check` services (advanced)

## Troubleshooting

| Issue                         | Check                                                          |
| ----------------------------- | -------------------------------------------------------------- |
| No real‑time updates          | WebSocket connectivity (network / port 8080)                   |
| Numbers not dialing           | Validate firmware accepted number; see `invalid number` errors |
| Ring pattern ignored          | Ensure pattern string syntax is valid                          |
| Blocked not working           | Confirm number format matches inbound (no trimming issue)      |
| Priority caller still blocked | Ensure DND force not misconfigured / number entered correctly  |

## License

MIT (see root project license).

---

### Appendix: Full Service List (Advanced)

In addition to core services listed earlier:

- `tsuryphone.get_call_history`
- `tsuryphone.clear_call_history`
- `tsuryphone.webhook_add / webhook_remove / webhook_clear / webhook_test`
- `tsuryphone.quick_dial_clear`
- `tsuryphone.blocked_clear`
- `tsuryphone.quick_dial_import / quick_dial_export`
- `tsuryphone.blocked_import / blocked_export`
- `tsuryphone.switch_call_waiting`
- `tsuryphone.get_diagnostics`
- `tsuryphone.get_missed_calls`
- `tsuryphone.set_ha_url`
- Resilience / diagnostics: `tsuryphone.resilience_status`, `tsuryphone.resilience_test`, `tsuryphone.websocket_reconnect`, `tsuryphone.run_health_check`

### Appendix: Event Naming Pattern

Phone state events use `tsuryphone_phone_state_<type>` (e.g. `..._dnd`). System events use `tsuryphone_system_<type>`.

### Appendix: Webhook Action Codes

When a code is dialed that maps to a webhook, integration fires `tsuryphone_webhook_action` (contains `actionId` / `code`). Use this to trigger automations without exposing a full number.
