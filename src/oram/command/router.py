"""oram.command.router — action dispatcher to engine and workers.

oram v2: adds listening routes, engine gateway, derivation, and layer modes.
validates actions, logs them, and routes to the appropriate handler.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import numpy as np

from oram.command.schemas import (
    AnalyzeMixAction,
    ApplyEffectAction,
    ClearLayerAction,
    ExportMixAction,
    ForkLayerAction,
    GenerateFromAction,
    GenerateLayerAction,
    KillAudioAction,
    ListenAction,
    ListenAgainAction,
    MuteLayerAction,
    OramAction,
    OverdubAction,
    QuitAction,
    RecordAction,
    RemoveEffectAction,
    ReplaceLayerAction,
    SaveSessionAction,
    SelectLayerAction,
    SetLayerModeAction,
    SetLoopRegionAction,
    SetModeAction,
    SetPanAction,
    SetVolumeAction,
    SoloLayerAction,
    StopRecordingAction,
    UnknownAction,
)
from oram.types import CommandLogEntry, LayerMode, Mode
from oram_security import redact_mapping, redact_text

if TYPE_CHECKING:
    from oram.audio.engine import MockAudioEngine
    from oram.audio.layer import LayerManager
    from oram.engines.registry import EngineRegistry
    from oram.engines.router import EngineRouter
    from oram.gateway.usage import UsageTracker
    from oram.types import OramSession


class ActionRouter:
    """routes validated actions to the engine and workers."""

    def __init__(
        self,
        session: OramSession,
        layer_manager: LayerManager,
        engine: MockAudioEngine,
        generator=None,
        analyzer=None,
        gateway=None,
        engine_registry: EngineRegistry | None = None,
        engine_router: EngineRouter | None = None,
        usage_tracker: UsageTracker | None = None,
        llm_adapter=None,
        config=None,
        session_dir: Path | str | None = None,
        on_status=None,
        on_before_layer_mutation=None,
    ):
        self.session = session
        self.layers = layer_manager
        self.engine = engine
        self.generator = generator
        self.analyzer = analyzer
        self.gateway = gateway  # legacy dict of ElevenLabs adapters (backward compat)
        self.engine_registry = engine_registry
        self.engine_router = engine_router
        self.usage_tracker = usage_tracker
        self.llm_adapter = llm_adapter
        self.config = config
        self.session_dir = Path(session_dir) if session_dir is not None else Path("./oram_sessions")
        self._archive_folder: Path | None = None
        self._pending_clear_target: int | str | None = None
        self._pending_clear_ts: float = 0.0  # monotonic timestamp
        self._on_status = on_status or (lambda msg: None)
        self._on_before_layer_mutation = on_before_layer_mutation
        self._quit_requested = False
        self._audio_kill_epoch = 0
        self._dsp_locks: dict[str, threading.Lock] = {}
        # v2: store last listening report per layer
        self._listening_reports: dict[str, object] = {}

    @property
    def quit_requested(self) -> bool:
        return self._quit_requested

    def emit_status(self, message: str) -> None:
        """publish a status message without routing an action."""
        self._on_status(message)

    @property
    def audio_kill_epoch(self) -> int:
        """monotonic token used to discard audio created after a kill request."""
        return self._audio_kill_epoch

    def is_audio_epoch_current(self, epoch: int) -> bool:
        return epoch == self._audio_kill_epoch

    def kill_all_audio(self) -> list[str]:
        """hard-silence ORAM without clearing layer buffers."""
        self._audio_kill_epoch += 1
        results: list[str] = []

        was_recording = bool(getattr(self.engine, "_recording", False))
        was_capturing = bool(getattr(self.engine, "_command_capture", False))
        if hasattr(self.engine, "stop_all_audio"):
            self.engine.stop_all_audio()
        else:
            if was_recording and hasattr(self.engine, "stop_recording"):
                self.engine.stop_recording()
            if was_capturing and hasattr(self.engine, "stop_command_capture"):
                self.engine.stop_command_capture()

        if was_recording:
            results.append("stopped recording")
        if was_capturing:
            results.append("stopped command capture")

        results.extend(self.layers.silence_all())
        self.session.listening = False
        self.session.mode = Mode.RECORD
        return results

    def route(self, action: OramAction, raw_text: str | None = None) -> str:
        """route an action to the appropriate handler. returns status message."""
        entry = CommandLogEntry(
            timestamp=datetime.now(timezone.utc),
            raw_text=redact_text(raw_text),
            action_json=redact_mapping(action.model_dump()),
            status="ok",
            message="",
        )

        try:
            message = self._dispatch(action)
            message = redact_text(message)
            entry.message = message
            entry.status = "ok"
        except Exception as e:
            message = redact_text(f"error: {e}")
            entry.status = "error"
            entry.message = message

        self.session.commands.append(entry)
        self._on_status(message)
        return message

    def _dispatch(self, action: OramAction) -> str:
        """dispatch to specific handler."""
        handlers = {
            RecordAction: self._handle_record,
            StopRecordingAction: lambda a: self._handle_stop_recording(),
            KillAudioAction: lambda a: self._handle_kill_audio(),
            OverdubAction: self._handle_overdub,
            SelectLayerAction: self._handle_select,
            MuteLayerAction: self._handle_mute,
            SoloLayerAction: self._handle_solo,
            ClearLayerAction: self._handle_clear,
            SetVolumeAction: self._handle_set_volume,
            SetPanAction: self._handle_set_pan,
            ApplyEffectAction: self._handle_apply_effect,
            RemoveEffectAction: self._handle_remove_effect,
            GenerateLayerAction: self._handle_generate,
            # v2 actions
            ListenAction: self._handle_listen,
            GenerateFromAction: self._handle_generate_from,
            ReplaceLayerAction: self._handle_replace,
            ForkLayerAction: self._handle_fork,
            ListenAgainAction: self._handle_listen_again,
            SetLayerModeAction: self._handle_set_layer_mode,
            SetLoopRegionAction: self._handle_set_loop_region,
            # shared
            AnalyzeMixAction: self._handle_analyze,
            SaveSessionAction: lambda a: self._handle_save(),
            ExportMixAction: lambda a: self._handle_export(),
            SetModeAction: self._handle_set_mode,
            QuitAction: lambda a: self._handle_quit(),
            UnknownAction: lambda a: f"unknown command: \"{a.raw_text or a.reason}\"",
        }

        handler = handlers.get(type(action))
        if handler:
            return handler(action)
        return "unhandled action"

    # --- transport ---

    def _handle_record(self, action: RecordAction) -> str:
        duration = self._duration_from_action(action.duration, action.bars)
        if action.bars is not None and duration is None:
            return "cannot record bars without bpm"

        target = self._target_for_recording(action.target)
        if action.target != "selected" and target is None:
            return f"invalid layer target: {action.target}"
        if target is not None:
            self.layers.select(target)

        self.session.mode = Mode.RECORD
        self.engine.start_recording(
            target=target,
            duration_seconds=duration,
            overdub=action.overdub,
        )
        dur = f" for {duration:.1f}s" if duration else ""
        return f"recording layer {self.layers.selected + 1}{dur}"

    def _handle_stop_recording(self) -> str:
        captured = self.engine.stop_recording()
        self.session.mode = Mode.RECORD  # stay in recorder mode
        layer = self.layers.selected_layer
        if not layer.is_empty:
            layer.compute_waveform()
        if captured is None:
            return "recording stopped (no audio captured)"
        return "recording stopped"

    def _handle_kill_audio(self) -> str:
        results = self.kill_all_audio()
        return "killed all audio" if results else "audio already silent"

    def _handle_overdub(self, action: OverdubAction) -> str:
        target = self._target_for_recording(action.target)
        if action.target != "selected" and target is None:
            return f"invalid layer target: {action.target}"
        if target is not None:
            self.layers.select(target)

        self.session.mode = Mode.RECORD
        duration = self._duration_from_action(action.duration, None)
        self.engine.start_recording(
            target=target,
            duration_seconds=duration,
            overdub=True,
        )
        return f"overdubbing layer {self.layers.selected + 1}"

    # --- selection / mute / solo / clear ---

    def _handle_select(self, action: SelectLayerAction) -> str:
        self.layers.select(action.target)
        self.session.selected_layer = self.layers.selected
        return f"selected layer {action.target}"

    def _handle_mute(self, action: MuteLayerAction) -> str:
        layer = self.layers.get_layer(action.target)
        self.layers.mute(layer)
        state = "muted" if layer.muted else "unmuted"
        return f"layer {layer.slot + 1} {state}"

    def _handle_solo(self, action: SoloLayerAction) -> str:
        layer = self.layers.get_layer(action.target)
        self.layers.solo(layer)
        state = "solo" if layer.solo else "unsolo"
        return f"layer {layer.slot + 1} {state}"

    def _handle_clear(self, action: ClearLayerAction) -> str:
        layer = self.layers.get_layer(action.target)
        if layer.is_empty:
            return f"layer {layer.slot + 1} is empty"
        pending_target = layer.slot + 1
        now = time.monotonic()
        # expire stale confirmations after 5 seconds
        if (
            not action.confirmed
            and (
                self._pending_clear_target != pending_target
                or (now - self._pending_clear_ts) > 5.0
            )
        ):
            self._pending_clear_target = pending_target
            self._pending_clear_ts = now
            return f"confirm clear layer {layer.slot + 1} by repeating clear"
        self._pending_clear_target = None
        self._pending_clear_ts = 0.0
        self.layers.clear(layer)
        return f"layer {layer.slot + 1} cleared"

    # --- mix ---

    def _handle_set_volume(self, action: SetVolumeAction) -> str:
        if action.target == "all":
            for layer in self.layers.layers:
                if not layer.is_empty:
                    layer.volume = action.volume
            return f"all layer volumes: {action.volume:.2f}"
        layer = self.layers.get_layer(action.target)
        layer.volume = action.volume
        return f"layer {layer.slot + 1} volume: {action.volume:.2f}"

    def _handle_set_pan(self, action: SetPanAction) -> str:
        if action.target == "all":
            for layer in self.layers.layers:
                if not layer.is_empty:
                    layer.pan = action.pan
            return f"all layer pans: {action.pan:.2f}"
        layer = self.layers.get_layer(action.target)
        layer.pan = action.pan
        return f"layer {layer.slot + 1} pan: {action.pan:.2f}"

    # --- effects ---

    def _handle_apply_effect(self, action: ApplyEffectAction) -> str:
        if action.target == "all":
            targets = [layer for layer in self.layers.layers if not layer.is_empty]
            if not targets:
                return "no active layers"

            if action.effect == "fade_out" and action.parameters.fade_seconds == 0.0:
                for layer in targets:
                    layer.volume = max(0.0, layer.volume * 0.8)
                return "all layers softer"

            for target in targets:
                threading.Thread(
                    target=self._apply_dsp,
                    args=(target, action),
                    daemon=True,
                ).start()
            return f"applying {action.effect} to all layers"

        layer = self.layers.get_layer(action.target)

        if layer.is_empty and action.target != "all":
            return f"layer {layer.slot + 1} is empty"

        threading.Thread(
            target=self._apply_dsp,
            args=(layer, action),
            daemon=True,
        ).start()

        return f"applying {action.effect} to layer {layer.slot + 1}"

    def _apply_dsp(self, layer, action: ApplyEffectAction) -> None:
        """apply DSP transform in a worker thread, then swap buffer."""
        lock = self._dsp_locks.setdefault(layer.id, threading.Lock())
        with lock:
            self._apply_dsp_locked(layer, action)

    def _apply_dsp_locked(self, layer, action: ApplyEffectAction) -> None:
        """run one DSP transform for a layer after per-layer serialization."""
        try:
            from oram.dsp.fades import fade_in, fade_out, trim_end, trim_start
            from oram.dsp.filter import highpass, lowpass
            from oram.dsp.granular import granular, stretch_breathe
            from oram.dsp.pitch import pitch_shift
            from oram.dsp.reverb import reverb, spatial_far
            from oram.dsp.reverse import reverse
            from oram.dsp.safety import crossfade_from_reference, prepare_dsp_output

            if action.effect == "speed":
                speed = action.parameters.speed or 1.0
                self._apply_playback_speed(layer, speed)
                self._on_status(f"applied {action.effect} to layer {layer.slot + 1}")
                return

            with layer._buf_lock:
                source_buf = layer.buffer.copy()
                source_length = layer.length_samples

            if source_buf.shape[0] == 0:
                self._on_status(f"dsp error: layer {layer.slot + 1} has no audio")
                return

            buf = source_buf
            sr = layer.sample_rate
            params = action.parameters
            metadata_updates: list[tuple[str, object]] = []

            if action.effect == "reverse":
                buf = reverse(buf)
                metadata_updates.append(("toggle_reverse", True))
            elif action.effect == "pitch":
                semitones = params.semitones or 0.0
                buf = pitch_shift(buf, semitones, sr)
                metadata_updates.append(("pitch", semitones))
            elif action.effect == "lowpass":
                cutoff = params.cutoff_hz or 2000.0
                buf = lowpass(buf, cutoff, sr)
                metadata_updates.extend([("filter_type", "lowpass"), ("filter_cutoff_hz", cutoff)])
            elif action.effect == "highpass":
                cutoff = params.cutoff_hz or 4000.0
                buf = highpass(buf, cutoff, sr)
                metadata_updates.extend([("filter_type", "highpass"), ("filter_cutoff_hz", cutoff)])
            elif action.effect == "reverb":
                wet = params.wet or 0.4
                decay = params.decay or "medium"
                buf = reverb(buf, wet=wet, decay=decay, sample_rate=sr)
                metadata_updates.append(("reverb_amount", wet))
            elif action.effect == "granular":
                density = params.density or 0.3
                grain_ms = params.grain_size_ms or 120.0
                jitter = params.jitter or 0.15
                buf = granular(buf, density=density, grain_size_ms=grain_ms,
                               jitter=jitter, sample_rate=sr)
                metadata_updates.extend([
                    ("grain_density", density),
                    ("grain_size_ms", grain_ms),
                    ("grain_jitter", jitter),
                ])
            elif action.effect == "fade_in":
                buf = fade_in(buf, sample_rate=sr)
            elif action.effect == "fade_out":
                secs = params.fade_seconds if params.fade_seconds is not None else 1.0
                buf = fade_out(buf, duration_seconds=secs, sample_rate=sr)
            elif action.effect == "trim_start":
                buf = trim_start(buf)
            elif action.effect == "trim_end":
                buf = trim_end(buf)
            elif action.effect == "spatial_far":
                buf = spatial_far(buf, sr)
            elif action.effect == "stretch_breathe":
                buf = stretch_breathe(buf, sr)

            fade_edges = action.effect not in {"fade_in", "fade_out"}
            buf = prepare_dsp_output(buf, sample_rate=sr, fade_edges=fade_edges)
            if buf.shape[0] == 0:
                self._on_status(f"dsp error: {action.effect} returned no audio")
                return

            # Use the latest playhead before the live swap.  This keeps playback
            # moving and pre-bakes a tiny dry-to-wet ramp at the handoff point.
            with layer._buf_lock:
                current_playhead = layer.playhead
            new_playhead = self.layers._project_playhead(
                current_playhead,
                source_length,
                buf.shape[0],
            )
            buf = crossfade_from_reference(
                buf,
                source_buf,
                processed_start=new_playhead,
                reference_start=current_playhead,
                sample_rate=sr,
            )

            self.layers.swap_buffer(
                layer,
                buf,
                preserve_playhead=True,
                source_playhead=current_playhead,
                source_length=source_length,
                metadata_update=lambda target: self._commit_dsp_metadata(
                    target,
                    action.effect,
                    metadata_updates,
                ),
            )

            self._on_status(f"applied {action.effect} to layer {layer.slot + 1}")

        except Exception as e:
            self._on_status(f"dsp error: {e}")

    @staticmethod
    def _apply_playback_speed(layer, speed: float) -> None:
        speed = max(0.25, min(4.0, float(speed or 1.0)))
        with layer._buf_lock:
            if layer.is_empty:
                raise ValueError(f"layer {layer.slot + 1} has no audio")
            layer.speed *= speed
            layer.speed = max(0.25, min(4.0, float(layer.speed)))
            if "speed" not in layer.effects_applied:
                layer.effects_applied.append("speed")
            layer.waveform_revision += 1

    @staticmethod
    def _commit_dsp_metadata(layer, effect: str, updates: list[tuple[str, object]]) -> None:
        for name, value in updates:
            if name == "toggle_reverse":
                layer.reverse = not layer.reverse
            elif name == "speed":
                layer.speed *= float(value)
            elif name == "pitch":
                layer.pitch_semitones += float(value)
            else:
                setattr(layer, name, value)
        if effect not in layer.effects_applied:
            layer.effects_applied.append(effect)

    def _handle_remove_effect(self, action: RemoveEffectAction) -> str:
        layer = self.layers.get_layer(action.target)
        if action.effect in layer.effects_applied:
            layer.effects_applied.remove(action.effect)
        return f"removed {action.effect} from layer {layer.slot + 1}"

    # --- v2: listening ---

    def _handle_listen(self, action: ListenAction) -> str:
        """listen to a layer through a configured route."""
        layer = self.layers.get_layer(action.target)
        if layer.is_empty:
            return f"layer {layer.slot + 1} is empty — nothing to listen to"

        self.session.mode = Mode.LISTEN
        self._on_status(f"listening to layer {layer.slot + 1} ({action.route})...")

        threading.Thread(
            target=self._listen_worker,
            args=(layer, action.route),
            daemon=True,
        ).start()

        return f"listening to layer {layer.slot + 1} via {action.route} route"

    def _listen_worker(self, layer, route_name: str) -> None:
        """run listening analysis in background."""
        try:
            from oram.ears.routes import create_route
            route = create_route(route_name, llm_adapter=self.llm_adapter)
            snapshot = self._snapshot_layer_for_generation(layer)
            report = route.listen(snapshot.buffer, snapshot.sample_rate)
            self._listening_reports[layer.id] = report

            # format observations
            tech = report.technical
            parts = []
            if tech.texture:
                parts.append(tech.texture)
            if tech.noise_balance:
                parts.append(tech.noise_balance)
            if tech.transient_type:
                parts.append(f"{tech.transient_type} transients")
            if report.descriptive.resembles:
                parts.append(report.descriptive.resembles)
            if report.speculative.imaginary_thing:
                parts.append(report.speculative.imaginary_thing[:60])

            observation = ", ".join(parts) if parts else "analyzing..."
            self._on_status(f"oram hears: {observation}")

        except Exception as e:
            self._on_status(f"listening error: {e}")

    # --- v2: generate from listening ---

    def _handle_generate_from(self, action: GenerateFromAction) -> str:
        """listen → compile prompt → generate → create layer."""
        layer = self.layers.get_layer(action.target)
        if layer.is_empty:
            return f"layer {layer.slot + 1} is empty"

        empty = self.layers.find_empty_layer()
        if empty is None:
            return "no empty layer slots available"

        self.session.mode = Mode.SUMMON
        self._on_status(f"listening and generating from layer {layer.slot + 1}...")

        threading.Thread(
            target=self._generate_from_worker,
            args=(
                layer,
                action.route,
                action.engine,
                action.duration,
                self._audio_kill_epoch,
                action.provider,
                action.intent,
            ),
            daemon=True,
        ).start()

        return f"generating from layer {layer.slot + 1} ({action.route} → {action.engine})"

    def _generate_from_worker(
        self,
        source_layer,
        route_name: str,
        engine_mode: str,
        duration: float | None,
        audio_epoch: int,
        provider: str = "",
        intent: str = "auto",
    ) -> None:
        """listen + compile + generate in background."""
        try:
            if not self.is_audio_epoch_current(audio_epoch):
                return

            from oram.ears.prompt_compiler import compile_prompt
            from oram.ears.routes import create_route
            from oram.gateway.router import select_engine

            # 1. listen
            source_snapshot = self._snapshot_layer_for_generation(source_layer)
            route = create_route(route_name, llm_adapter=self.llm_adapter)
            report = route.listen(source_snapshot.buffer, source_snapshot.sample_rate)
            self._listening_reports[source_layer.id] = report

            # 2. select engine
            analysis_data = {
                "contains_speech": report.technical.contains_speech,
                "contains_voice": report.technical.contains_voice,
                "pitch_confidence": report.technical.pitch_confidence,
                "rhythmic_regularity": report.technical.rhythmic_regularity,
                "is_noisy": report.technical.is_noisy,
                "is_gestural": report.technical.is_gestural,
                "duration": report.technical.duration,
            }
            decision = select_engine(analysis_data, engine_mode)
            self._on_status(f"engine: {decision.engine} — {decision.reason}")

            # 3. compile prompt with mix context
            mix_ctx = None
            try:
                from oram.ears.mix_context import build_mix_context
                active_layers = [
                    self._snapshot_layer_for_generation(l)
                    for l in self.layers.layers
                    if not l.is_empty and not l.muted
                ]
                if active_layers:
                    mix_ctx = build_mix_context(active_layers, source_snapshot.sample_rate)
            except Exception:
                pass  # mix context is optional

            prompt = compile_prompt(report, decision.engine, mix_context=mix_ctx)
            self._on_status(f"prompt: {prompt[:100]}...")

            if not self.is_audio_epoch_current(audio_epoch):
                self._on_status("generation discarded after kill")
                return

            # 4. generate
            gen_duration = self._clamp_duration(
                duration or min(source_snapshot.duration_seconds * 1.2, 30.0),
                kind="generated",
            )
            audio = self._call_engine(
                decision.engine,
                prompt,
                gen_duration,
                source_snapshot,
                intent=intent,
                provider=provider,
            )

            if audio is None:
                self._on_status("generation failed: no audio returned")
                return

            if not self.is_audio_epoch_current(audio_epoch):
                self._on_status("generation discarded after kill")
                return

            # 5. create derived layer
            target_layer = self.layers.find_empty_layer()
            if target_layer is None:
                self._on_status("no empty layer slot for generated audio")
                return
            self._before_layer_mutation("generate from layer", target_layer)
            new_layer = self.layers.assign_generated_buffer(
                target_layer,
                audio,
                prompt=prompt,
                parent=source_layer,
                route=route_name,
                engine=decision.engine,
            )

            self.session.mode = Mode.RECORD
            self._on_status(
                f"generated → layer {new_layer.slot + 1} "
                f"(from layer {source_layer.slot + 1}, depth {new_layer.generation_depth})"
            )

        except Exception as e:
            self.session.mode = Mode.RECORD
            self._on_status(f"generation error: {e}")

    def _call_engine(
        self,
        engine: str,
        prompt: str,
        duration: float,
        source_layer=None,
        intent: str = "auto",
        provider: str = "",
        parameters: dict | None = None,
        allow_mock_fallback: bool = True,
    ) -> np.ndarray | None:
        """call the appropriate engine adapter via the EngineRouter.

        priority:
        1. engine router (new capability-based system)
        2. legacy gateway dict (backward compat)
        3. mock generator (fallback)
        """

        # 1. try the new engine router
        if self.engine_router is not None:
            try:
                from oram.engines.adapter import GenerationRequest
                from oram.engines.capabilities import EngineProvider, SonicIntent
                from oram.engines.normalizer import AudioNormalizer
                from oram.engines.router import resolve_intent

                engine_aliases = {
                    "stable-audio-2": "stability-stable-audio-2",
                    "stable-audio-2.5": "stability-stable-audio-25",
                    "stable-audio-3": "stability-stable-audio-3",
                    "sa3": "stable-audio-3-local",
                    "local-sa3": "stable-audio-3-local",
                    "local": "local-mock",
                }
                engine_id = engine_aliases.get(engine, engine)
                if not allow_mock_fallback and "-" in engine_id:
                    adapter = self.engine_registry.get(engine_id) if self.engine_registry is not None else None
                    if adapter is None or not adapter.is_available():
                        raise RuntimeError(f"engine {engine_id} unavailable")

                # resolve intent
                if intent != "auto" and intent != "":
                    sonic_intent = resolve_intent(intent)
                elif engine not in ("auto", ""):
                    sonic_intent = resolve_intent(engine)
                else:
                    sonic_intent = SonicIntent.SOUND_EFFECT

                provider_enum = None
                if provider and provider != "auto":
                    try:
                        provider_enum = EngineProvider(provider)
                    except ValueError:
                        provider_enum = None

                source_audio = None
                source_sample_rate = self.session.sample_rate
                if source_layer is not None:
                    lock = getattr(source_layer, "_buf_lock", None)
                    if lock is not None:
                        with lock:
                            source_audio = np.asarray(source_layer.buffer, dtype=np.float32).copy()
                            source_sample_rate = int(source_layer.sample_rate)
                    else:
                        source_audio = np.asarray(source_layer.buffer, dtype=np.float32).copy()
                        source_sample_rate = int(source_layer.sample_rate)

                # build request
                request = GenerationRequest(
                    prompt=prompt,
                    intent=sonic_intent,
                    duration_seconds=duration,
                    source_audio=source_audio,
                    source_sample_rate=source_sample_rate,
                    # if engine contains "-" it's a provider-specific ID
                    engine_id=engine_id if "-" in engine_id else None,
                    provider=provider_enum,
                    parameters=dict(parameters or {}),
                )

                # route and execute
                result = self.engine_router.execute(request)

                # track usage
                if self.usage_tracker and source_layer:
                    self.usage_tracker.record(
                        engine=result.engine_id,
                        layer_id=source_layer.id,
                        prompt=prompt,
                        duration_seconds=result.duration_seconds,
                        credits_used=result.cost_credits,
                    )

                # normalize output
                normalizer = AudioNormalizer(target_sr=self.session.sample_rate)
                return normalizer.normalize(
                    result.audio,
                    source_sr=result.sample_rate,
                    target_sr=self.session.sample_rate,
                )
            except Exception as e:
                self._on_status(f"engine router error: {e}")

        # 2. legacy gateway dict fallback
        if self.gateway and engine in self.gateway:
            try:
                adapter = self.gateway[engine]
                params = {"duration_seconds": duration}
                result = adapter.generate(prompt, params)

                # track usage
                if self.usage_tracker and source_layer:
                    self.usage_tracker.record(
                        engine=engine,
                        layer_id=source_layer.id,
                        prompt=prompt,
                        duration_seconds=result.duration_seconds,
                        credits_used=result.cost_credits,
                    )

                from oram.audio.resample import ensure_stereo_float32
                return ensure_stereo_float32(
                    result.audio,
                    source_sr=result.sample_rate,
                    target_sr=self.session.sample_rate,
                )
            except Exception as e:
                self._on_status(f"engine {engine} error: {e}")

        # 3. mock generator fallback
        if allow_mock_fallback and self.generator:
            try:
                safe_duration = self._clamp_duration(duration, kind="generated") or 0.5
                return self.generator.generate(prompt, safe_duration, self.session.sample_rate)
            except Exception as e:
                self._on_status(f"mock generator error: {e}")

        return None

    # --- v2: replace / fork / listen again ---

    def _handle_replace(self, action: ReplaceLayerAction) -> str:
        source = self.layers.get_layer(action.source)
        target = self.layers.get_layer(action.target)
        if source.is_empty:
            return "source layer is empty"
        self.layers.replace_layer_audio(target, source.buffer.copy())
        return f"replaced layer {target.slot + 1} with layer {source.slot + 1}"

    def _handle_fork(self, action: ForkLayerAction) -> str:
        source = self.layers.get_layer(action.target)
        if source.is_empty:
            return f"layer {source.slot + 1} is empty"
        forked = self.layers.fork_layer(source)
        if forked is None:
            return "no empty layer slots"
        return f"forked layer {source.slot + 1} → layer {forked.slot + 1}"

    def _handle_listen_again(self, action: ListenAgainAction) -> str:
        """re-listen and generate from a generated layer (recursive listening)."""
        return self._handle_generate_from(GenerateFromAction(
            target=action.target,
            route=action.route,
            engine=action.engine,
        ))

    # --- v2: layer mode ---

    def _handle_set_layer_mode(self, action: SetLayerModeAction) -> str:
        layer = self.layers.get_layer(action.target)
        try:
            mode = LayerMode(action.mode)
        except ValueError:
            return f"invalid layer mode: {action.mode}"
        self.layers.set_layer_mode(layer, mode)
        return f"layer {layer.slot + 1} mode: {mode.value}"

    def _handle_set_loop_region(self, action: SetLoopRegionAction) -> str:
        """set a layer's loop region from pct or seconds."""
        layer = self.layers.get_layer(action.target)
        if layer.is_empty:
            return f"layer {layer.slot + 1} is empty — cannot set loop region"

        length = layer.length_samples
        sr = layer.sample_rate

        # resolve start/end to sample offsets
        if action.start_seconds is not None and action.end_seconds is not None:
            start = int(action.start_seconds * sr)
            end = int(action.end_seconds * sr)
        elif action.start_pct is not None and action.end_pct is not None:
            start = int(action.start_pct / 100.0 * length)
            end = int(action.end_pct / 100.0 * length)
        else:
            # partial: fill missing with defaults
            start = int((action.start_pct or 0.0) / 100.0 * length) if action.start_pct is not None else (
                int(action.start_seconds * sr) if action.start_seconds is not None else 0
            )
            end = int((action.end_pct or 100.0) / 100.0 * length) if action.end_pct is not None else (
                int(action.end_seconds * sr) if action.end_seconds is not None else length
            )

        # clamp
        start = max(0, min(start, length))
        end = max(0, min(end, length))

        # enforce start < end
        if start >= end:
            return f"invalid loop region: start ({start}) must be before end ({end})"

        # enforce minimum duration (20ms or one block)
        min_samples = max(int(0.02 * sr), 1024)
        if (end - start) < min_samples:
            return f"loop region too short: minimum {min_samples / sr * 1000:.0f}ms"

        self.layers.set_loop_region(layer, start, end, action.enabled)

        start_sec = start / sr
        end_sec = end / sr
        dur_sec = (end - start) / sr
        state = "enabled" if action.enabled else "disabled"
        return f"loop {state}: layer {layer.slot + 1} [{start_sec:.2f}s–{end_sec:.2f}s] ({dur_sec:.2f}s)"

    # --- generation (v1 compat) ---

    def _handle_generate(self, action: GenerateLayerAction) -> str:
        if self.generator is None and not self.gateway and self.engine_router is None:
            return "generator not available"

        self.session.mode = Mode.SUMMON
        self._on_status(f"summon: generating \"{action.prompt}\"")

        threading.Thread(
            target=self._generate_worker,
            args=(action, self._audio_kill_epoch),
            daemon=True,
        ).start()

        return f"summoning: {action.prompt}"

    def _generate_worker(self, action: GenerateLayerAction, audio_epoch: int) -> None:
        """generate sound in a worker thread."""
        try:
            if not self.is_audio_epoch_current(audio_epoch):
                return

            engine = action.engine if action.engine != "auto" else "sfx"
            duration = self._clamp_duration(action.duration, kind="generated") or action.duration
            audio = self._call_engine(
                engine,
                action.prompt,
                duration,
                intent=action.intent,
                provider=action.provider,
            )

            if audio is None and self.generator:
                audio = self.generator.generate(
                    action.prompt, duration, self.session.sample_rate
                )

            if audio is None:
                self._on_status("generation failed")
                self.session.mode = Mode.RECORD
                return

            if not self.is_audio_epoch_current(audio_epoch):
                self._on_status("generation discarded after kill")
                self.session.mode = Mode.RECORD
                return

            target_layer = self.layers.find_empty_layer()
            if target_layer is None:
                self._on_status("generation failed: no empty layer slots available")
                self.session.mode = Mode.RECORD
                return

            self._before_layer_mutation("generate", target_layer)
            self.layers.assign_generated_buffer(
                target_layer,
                audio,
                prompt=action.prompt,
                engine=action.engine,
                volume=action.mix_level,
            )
            self.session.generated_bed_id = target_layer.slot
            self.session.mode = Mode.RECORD

            self._on_status(f"generated \"{action.prompt}\" → layer {target_layer.slot + 1}")

        except Exception as e:
            self.session.mode = Mode.RECORD
            self._on_status(f"generation failed: {e}")

    # --- analysis ---

    def _handle_analyze(self, action: AnalyzeMixAction) -> str:
        from oram.ears.analyzer import analyze_session
        report = analyze_session(self.session)
        observations = "; ".join(report.observations) if report.observations else "nothing to report"
        return f"oram hears: {observations}"

    # --- session ---

    def _handle_save(self) -> str:
        from oram.archive.session import create_session_folder

        folder = create_session_folder(
            self.session,
            self.layers,
            self.session_dir,
            self._archive_folder.name if self._archive_folder is not None else None,
        )
        self._archive_folder = folder

        # save usage if tracker exists
        if self.usage_tracker:
            self.usage_tracker.save(folder / "usage.json")

        return f"session saved: {folder}"

    def _handle_export(self) -> str:
        return self._handle_save()

    def _handle_set_mode(self, action: SetModeAction) -> str:
        try:
            self.session.mode = Mode(action.mode)
        except ValueError:
            return f"unknown mode: {action.mode}"
        return f"mode: {action.mode}"

    def _handle_quit(self) -> str:
        self._quit_requested = True
        return "oram shutting down"

    # --- helpers ---

    def _duration_from_action(self, duration: float | None, bars: int | None) -> float | None:
        if duration is not None:
            return self._clamp_duration(duration, kind="loop")
        if bars is None:
            return None
        if not self.session.bpm:
            return None
        beats = bars * 4
        return self._clamp_duration(beats * 60.0 / self.session.bpm, kind="loop")

    def _clamp_duration(self, duration: float | None, kind: str = "loop") -> float | None:
        """clamp requested durations before allocating buffers or calling gateways."""
        if duration is None:
            return None
        if self.config is not None and hasattr(self.config, "validate_duration"):
            return self.config.validate_duration(duration, kind=kind)
        max_duration = 120.0 if kind == "loop" else 60.0
        return max(0.0, min(duration, max_duration))

    def _before_layer_mutation(self, label: str, layer) -> None:
        """Notify the host just before a generated worker mutates a layer."""
        if self._on_before_layer_mutation is None:
            return
        try:
            self._on_before_layer_mutation(label, layer)
        except Exception as exc:
            self._on_status(f"history warning: {exc}")

    def _snapshot_layer_for_generation(self, layer, max_seconds: float = 12.0):
        """Copy a bounded layer window for listening, mix context, and source conditioning."""
        with layer._buf_lock:
            buffer = layer.buffer
            sample_rate = int(layer.sample_rate)
            channels = int(layer.channels)
            playhead = int(layer.playhead)
            duration_seconds = float(layer.duration_seconds)
            loop_enabled = bool(layer.looper.enabled)
            loop_start = int(layer.looper.start_offset)
            loop_end = int(layer.looper.end_offset)
            muted = bool(layer.muted)
            is_empty = bool(layer.is_empty)
            layer_id = layer.id
            slot = layer.slot

        audio = self._copy_generation_window(
            buffer,
            sample_rate=sample_rate,
            channels=channels,
            playhead=playhead,
            loop_enabled=loop_enabled,
            loop_start=loop_start,
            loop_end=loop_end,
            max_seconds=max_seconds,
        )
        return SimpleNamespace(
            id=layer_id,
            slot=slot,
            sample_rate=sample_rate,
            channels=channels,
            buffer=audio,
            duration_seconds=float(audio.shape[0]) / sample_rate if sample_rate > 0 else duration_seconds,
            muted=muted,
            is_empty=is_empty or audio.shape[0] == 0,
        )

    @staticmethod
    def _copy_generation_window(
        buffer: np.ndarray,
        *,
        sample_rate: int,
        channels: int,
        playhead: int,
        loop_enabled: bool,
        loop_start: int,
        loop_end: int,
        max_seconds: float,
    ) -> np.ndarray:
        length = int(buffer.shape[0])
        if length <= 0:
            return np.zeros((0, max(1, channels)), dtype=np.float32)

        max_samples = max(1, int(max(1, sample_rate) * max_seconds))
        if loop_enabled:
            start = max(0, min(loop_start, length - 1))
            end = loop_end if loop_end > start else length
            end = max(start + 1, min(end, length))
            segment = buffer[start:end]
            if segment.shape[0] > max_samples:
                segment = segment[:max_samples]
            return np.asarray(segment, dtype=np.float32).copy()

        if length <= max_samples:
            return np.asarray(buffer, dtype=np.float32).copy()

        start = int(playhead) % length
        end = start + max_samples
        if end <= length:
            return np.asarray(buffer[start:end], dtype=np.float32).copy()

        return np.concatenate((buffer[start:], buffer[:end - length]), axis=0).astype(np.float32, copy=False)

    def _target_for_recording(self, target: int | str) -> int | None:
        if target == "selected":
            return None
        if isinstance(target, int):
            return target if 1 <= target <= len(self.layers.layers) else None
        if isinstance(target, str) and target.isdigit():
            layer = int(target)
            return layer if 1 <= layer <= len(self.layers.layers) else None
        return None
