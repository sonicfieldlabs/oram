from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from threading import RLock
from uuid import uuid4

from oram_sa3_server.schemas import ControlCVProfile, ControlEvent, ControlPort, ControlRoute, ControlSnapshot
from oram_sa3_server.storage import StorageManager, utc_now_iso


CONTROL_EVENT_LIMIT = 512


def default_control_ports() -> list[ControlPort]:
    return [
        ControlPort(
            id="library:selected_audio",
            label="Selected audio",
            kind="audio",
            direction="output",
            scope="library",
            metadata={"source": "library"},
        ),
        ControlPort(
            id="library:selected_region",
            label="Selected region",
            kind="audio",
            direction="output",
            scope="library",
            metadata={"source": "waveform_region"},
        ),
        ControlPort(
            id="mod:audio_to_control",
            label="Audio-to-Control",
            kind="control",
            direction="output",
            scope="internal",
            min=0.0,
            max=1.0,
        ),
        ControlPort(
            id="mod:macro",
            label="Macro control",
            kind="control",
            direction="output",
            scope="internal",
            min=0.0,
            max=1.0,
        ),
        ControlPort(
            id="mod:gesture",
            label="Gesture recorder",
            kind="control",
            direction="output",
            scope="internal",
            min=0.0,
            max=1.0,
        ),
        ControlPort(
            id="time:clock",
            label="Clock pulse",
            kind="event",
            direction="output",
            scope="time",
            unit="tick",
        ),
        ControlPort(
            id="metadata:lineage",
            label="Lineage metadata",
            kind="metadata",
            direction="output",
            scope="metadata",
        ),
        ControlPort(
            id="midi:input",
            label="MIDI input",
            kind="midi",
            direction="output",
            scope="hardware",
            metadata={"requires_permission": True, "implementation": "browser_web_midi"},
        ),
        ControlPort(
            id="osc:input",
            label="OSC input",
            kind="osc",
            direction="output",
            scope="network",
            metadata={"implementation": "bridge_pending"},
        ),
        ControlPort(
            id="generation:prompt_weight",
            label="Generation prompt weight",
            kind="control",
            direction="input",
            scope="generation",
            min=0.25,
            max=1.5,
        ),
        ControlPort(
            id="generation:seed_drift",
            label="Generation seed drift",
            kind="control",
            direction="input",
            scope="generation",
            min=0.0,
            max=1.0,
        ),
        ControlPort(
            id="generation:batch_spread",
            label="Generation batch spread",
            kind="control",
            direction="input",
            scope="generation",
            min=0.0,
            max=1.0,
        ),
        ControlPort(
            id="generation:inpaint_density",
            label="Generation inpaint density",
            kind="control",
            direction="input",
            scope="generation",
            min=0.0,
            max=1.0,
        ),
        ControlPort(
            id="generation:brightness_language",
            label="Generation brightness language",
            kind="control",
            direction="input",
            scope="generation",
            min=-0.85,
            max=0.85,
        ),
        ControlPort(
            id="generation:continuation_divergence",
            label="Generation continuation divergence",
            kind="control",
            direction="input",
            scope="generation",
            min=0.2,
            max=1.2,
        ),
        ControlPort(
            id="time:event_velocity",
            label="Time event velocity",
            kind="control",
            direction="input",
            scope="time",
            min=0.0,
            max=2.0,
        ),
        ControlPort(
            id="time:event_probability",
            label="Time event probability",
            kind="control",
            direction="input",
            scope="time",
            min=0.0,
            max=1.0,
        ),
        ControlPort(
            id="midi:cc_output",
            label="MIDI CC output",
            kind="midi",
            direction="input",
            scope="hardware",
            metadata={"requires_permission": True, "implementation": "browser_web_midi"},
        ),
        ControlPort(
            id="osc:output",
            label="OSC output",
            kind="osc",
            direction="input",
            scope="network",
            metadata={"implementation": "safe_bridge_config_only"},
        ),
        ControlPort(
            id="cv:export",
            label="CV-safe render export",
            kind="cv",
            direction="input",
            scope="export",
            min=-1.0,
            max=1.0,
            metadata={
                "hardware_output": False,
                "speaker_protection": True,
                "description": "Writes a control WAV artifact only.",
            },
        ),
    ]


DEFAULT_CONTROL_PORTS = tuple(default_control_ports())


class ControlRegistry:
    def __init__(self, storage: StorageManager) -> None:
        self.storage = storage
        self.control_dir = storage.settings.output_root / "control"
        self.routes_path = self.control_dir / "routes.json"
        self.events_path = self.control_dir / "events.json"
        self.cv_profiles_path = self.control_dir / "cv_profiles.json"
        self._lock = RLock()
        self._events: deque[ControlEvent] = deque(maxlen=CONTROL_EVENT_LIMIT)
        self.control_dir.mkdir(parents=True, exist_ok=True)
        self._load_events_into_memory()

    def ports(self) -> list[ControlPort]:
        return [port.model_copy(deep=True) for port in DEFAULT_CONTROL_PORTS]

    def list_routes(self) -> list[ControlRoute]:
        with self._lock:
            return self._load_routes()

    def save_route(self, route: ControlRoute) -> ControlRoute:
        now = utc_now_iso()
        self._validate_route_ports(route)
        with self._lock:
            routes = self._load_routes()
            route = route.model_copy(
                update={
                    "id": route.id or f"route_{uuid4().hex[:12]}",
                    "created_at": route.created_at or now,
                    "updated_at": now,
                }
            )
            next_routes = [item for item in routes if item.id != route.id]
            next_routes.append(route)
            self._write_routes(next_routes)
        self.add_event(
            ControlEvent(
                route_id=route.id,
                port_id=route.target_port_id,
                kind="event",
                source="control_registry",
                value={"action": "route_saved", "enabled": route.enabled},
                metadata={"target_kind": route.target_kind, "source_kind": route.source_kind},
            )
        )
        return route

    def set_route_enabled(self, route_id: str, enabled: bool) -> ControlRoute:
        with self._lock:
            routes = self._load_routes()
            for index, route in enumerate(routes):
                if route.id == route_id:
                    updated = route.model_copy(
                        update={"enabled": enabled, "updated_at": utc_now_iso()}
                    )
                    routes[index] = updated
                    self._write_routes(routes)
                    break
            else:
                raise KeyError(route_id)
        self.add_event(
            ControlEvent(
                route_id=route_id,
                kind="event",
                source="control_registry",
                value={"action": "route_enabled", "enabled": enabled},
            )
        )
        return updated

    def delete_route(self, route_id: str) -> None:
        with self._lock:
            routes = self._load_routes()
            next_routes = [item for item in routes if item.id != route_id]
            if len(next_routes) == len(routes):
                raise KeyError(route_id)
            self._write_routes(next_routes)
        self.add_event(
            ControlEvent(
                route_id=route_id,
                kind="event",
                source="control_registry",
                value={"action": "route_deleted"},
            )
        )

    def events(self) -> list[ControlEvent]:
        with self._lock:
            return list(self._events)

    def add_event(self, event: ControlEvent) -> ControlEvent:
        event = event.model_copy(
            update={
                "id": event.id or f"event_{uuid4().hex[:12]}",
                "timestamp": event.timestamp or utc_now_iso(),
            }
        )
        with self._lock:
            self._events.append(event)
            self._write_events(list(self._events))
        return event

    def snapshot(self, metadata: dict | None = None) -> ControlSnapshot:
        return ControlSnapshot(
            id=f"snapshot_{uuid4().hex[:12]}",
            captured_at=utc_now_iso(),
            routes=self.list_routes(),
            events=self.events(),
            metadata=metadata or {},
        )

    def panic(self) -> ControlEvent:
        with self._lock:
            profiles = [
                profile.model_copy(update={"armed": False, "updated_at": utc_now_iso()})
                for profile in self._load_cv_profiles()
            ]
            self._write_cv_profiles(profiles)
        return self.add_event(
            ControlEvent(
                kind="event",
                source="panic",
                value={"action": "panic_zero", "hardware_output": False},
                metadata={"cv_output": "disarmed", "midi_output": "not_enabled"},
            )
        )

    def list_cv_profiles(self) -> list[ControlCVProfile]:
        with self._lock:
            return self._load_cv_profiles()

    def save_cv_profile(self, profile: ControlCVProfile) -> ControlCVProfile:
        now = utc_now_iso()
        with self._lock:
            profiles = self._load_cv_profiles()
            profile = profile.model_copy(
                update={
                    "id": profile.id or f"cv_profile_{uuid4().hex[:12]}",
                    "created_at": profile.created_at or now,
                    "updated_at": now,
                    "armed": profile.armed and profile.calibrated and profile.speaker_protection,
                }
            )
            next_profiles = [item for item in profiles if item.id != profile.id]
            next_profiles.append(profile)
            self._write_cv_profiles(next_profiles)
        self.add_event(
            ControlEvent(
                kind="cv",
                source="cv_profile",
                value={
                    "action": "profile_saved",
                    "profile_id": profile.id,
                    "calibrated": profile.calibrated,
                    "armed": profile.armed,
                },
            )
        )
        return profile

    def set_cv_profile_armed(self, profile_id: str, armed: bool, *, confirm: bool) -> ControlCVProfile:
        with self._lock:
            profiles = self._load_cv_profiles()
            for index, profile in enumerate(profiles):
                if profile.id != profile_id:
                    continue
                if armed and not confirm:
                    raise PermissionError("arming a CV profile requires explicit confirmation")
                if armed and (not profile.calibrated or not profile.speaker_protection):
                    raise ValueError("CV profile must be calibrated and speaker-protected before arming")
                updated = profile.model_copy(update={"armed": armed, "updated_at": utc_now_iso()})
                profiles[index] = updated
                self._write_cv_profiles(profiles)
                break
            else:
                raise KeyError(profile_id)
        self.add_event(
            ControlEvent(
                kind="cv",
                source="cv_profile",
                value={"action": "profile_armed", "profile_id": profile_id, "armed": armed},
            )
        )
        return updated

    def _load_routes(self) -> list[ControlRoute]:
        if not self.routes_path.exists():
            return []
        try:
            data = json.loads(self.routes_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        raw_routes = data.get("routes") if isinstance(data, dict) else data
        if not isinstance(raw_routes, list):
            return []
        routes: list[ControlRoute] = []
        for raw in raw_routes:
            try:
                routes.append(ControlRoute(**raw))
            except Exception:
                continue
        return routes

    def _write_routes(self, routes: list[ControlRoute]) -> None:
        payload = {
            "updated_at": utc_now_iso(),
            "routes": [route.model_dump(mode="json") for route in routes],
        }
        self.storage.write_json_atomic(self.routes_path, payload)

    def _load_events_into_memory(self) -> None:
        for event in self._load_events():
            self._events.append(event)

    def _load_events(self) -> list[ControlEvent]:
        if not self.events_path.exists():
            return []
        try:
            data = json.loads(self.events_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        raw_events = data.get("events") if isinstance(data, dict) else data
        if not isinstance(raw_events, list):
            return []
        events: list[ControlEvent] = []
        for raw in raw_events[-CONTROL_EVENT_LIMIT:]:
            try:
                events.append(ControlEvent(**raw))
            except Exception:
                continue
        return events

    def _write_events(self, events: list[ControlEvent]) -> None:
        payload = {
            "updated_at": utc_now_iso(),
            "events": [event.model_dump(mode="json") for event in events[-CONTROL_EVENT_LIMIT:]],
        }
        self.storage.write_json_atomic(self.events_path, payload)

    def _load_cv_profiles(self) -> list[ControlCVProfile]:
        if not self.cv_profiles_path.exists():
            return []
        try:
            data = json.loads(self.cv_profiles_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        raw_profiles = data.get("profiles") if isinstance(data, dict) else data
        if not isinstance(raw_profiles, list):
            return []
        profiles: list[ControlCVProfile] = []
        for raw in raw_profiles:
            try:
                profiles.append(ControlCVProfile(**raw))
            except Exception:
                continue
        return profiles

    def _write_cv_profiles(self, profiles: list[ControlCVProfile]) -> None:
        payload = {
            "updated_at": utc_now_iso(),
            "profiles": [profile.model_dump(mode="json") for profile in profiles],
        }
        self.storage.write_json_atomic(self.cv_profiles_path, payload)

    def _validate_route_ports(self, route: ControlRoute) -> None:
        ports = {port.id: port for port in self.ports()}
        source = ports.get(route.source_port_id)
        target = ports.get(route.target_port_id)
        if not source:
            raise ValueError(f"unknown source control port: {route.source_port_id}")
        if not target:
            raise ValueError(f"unknown target control port: {route.target_port_id}")
        if source.direction != "output":
            raise ValueError(f"source control port is not an output: {route.source_port_id}")
        if target.direction != "input":
            raise ValueError(f"target control port is not an input: {route.target_port_id}")
        if source.kind != route.source_kind:
            raise ValueError(f"source kind must be {source.kind}")
        if target.kind != route.target_kind:
            raise ValueError(f"target kind must be {target.kind}")

    def resolve_control_artifact(self, path: str | Path) -> Path:
        resolved = self.storage.resolve_existing_path(path, label="control artifact")
        try:
            resolved.relative_to(self.control_dir.resolve())
        except ValueError as exc:
            raise PermissionError(f"control artifact must be inside {self.control_dir}") from exc
        return resolved
