"""oram application — main run loop.

oram v2: recorder-first workflow, ElevenLabs gateway, listening routes.
wires together: engine, layers, parser, router, TUI, STT, gateway.
"""

from __future__ import annotations

import sys
from datetime import datetime

from rich.console import Console
from rich.live import Live

from oram.agent.controller import AgentController
from oram.agent.llm_adapter import LLMCliAdapter
from oram.audio.engine import MockAudioEngine
from oram.audio.layer import LayerManager
from oram.command.keyboard import key_to_action
from oram.command.push_to_talk import PushToTalk
from oram.command.router import ActionRouter
from oram.command.schemas import (
    ForkLayerAction,
    GenerateFromAction,
    ListenAction,
    SetLayerModeAction,
    SetModeAction,
)
from oram.config import OramConfig
from oram.engines.registry import EngineRegistry
from oram.engines.router import EngineRouter
from oram.gateway.usage import UsageTracker
from oram.stt.mock import MockSTTAdapter
from oram.summon.mock import MockSoundGenerator
from oram.tui.app import OramTUI
from oram.tui.theme import ORAM_THEME
from oram.types import OramSession


def _build_gateway(config: OramConfig) -> dict | None:
    """build legacy ElevenLabs engine gateway dict from config.

    kept for backward compat — the new EngineRegistry is preferred.
    """
    if config.generator_backend not in ("elevenlabs", "auto"):
        return None
    if not config.elevenlabs_api_key:
        return None

    gateway = {}
    try:
        from oram.gateway.sfx import SFXAdapter
        gateway["sfx"] = SFXAdapter(api_key=config.elevenlabs_api_key)
    except Exception:
        pass

    try:
        from oram.gateway.voice import VoiceAdapter
        gateway["voice"] = VoiceAdapter(api_key=config.elevenlabs_api_key)
    except Exception:
        pass

    try:
        from oram.gateway.music import MusicAdapter
        gateway["music"] = MusicAdapter(api_key=config.elevenlabs_api_key)
    except Exception:
        pass

    return gateway if gateway else None


def run(config: OramConfig) -> None:
    """start the oram instrument."""
    console = Console(theme=ORAM_THEME)

    # create session
    session_name = config.session_name or f"oram_{datetime.now().strftime('%H%M%S')}"
    session = OramSession(
        id=session_name,
        scene=session_name,
        sample_rate=config.sample_rate,
        auto_listen=config.auto_listen,
    )

    # layer manager
    layer_manager = LayerManager(
        sample_rate=config.sample_rate,
        channels=config.channels_out,
    )
    session.layers = layer_manager.layers

    # audio engine
    use_mock = config.mock_audio
    if not use_mock:
        try:
            from oram.audio.realtime import RealAudioEngine

            engine = RealAudioEngine(
                session=session,
                layer_manager=layer_manager,
                sample_rate=config.sample_rate,
                block_size=config.block_size,
                input_device=config.input_device,
                output_device=config.output_device,
            )
        except Exception as e:
            console.print(f"audio: real failed ({e}), using mock", style="oram.status")
            use_mock = True

    if use_mock:
        engine = MockAudioEngine(
            session=session,
            layer_manager=layer_manager,
            sample_rate=config.sample_rate,
            block_size=config.block_size,
        )

    # STT adapter
    stt = None
    if not config.no_stt:
        if config.stt_backend == "whisper":
            try:
                from oram.stt.whisper_local import WhisperLocalAdapter
                stt = WhisperLocalAdapter()
                console.print("stt: whisper local", style="oram.status")
            except ImportError:
                console.print("stt: whisper not available, falling back to mock", style="oram.status")
                stt = MockSTTAdapter()
        elif config.stt_backend == "elevenlabs":
            try:
                from oram.stt.elevenlabs import ElevenLabsSTTAdapter
                stt = ElevenLabsSTTAdapter(api_key=config.elevenlabs_api_key)
                if stt.is_available():
                    console.print("stt: elevenlabs scribe", style="oram.status")
                else:
                    console.print("stt: elevenlabs key missing, falling back to mock", style="oram.status")
                    stt = MockSTTAdapter()
            except Exception as e:
                console.print(f"stt: elevenlabs unavailable ({e}), falling back to mock", style="oram.status")
                stt = MockSTTAdapter()
        else:
            stt = MockSTTAdapter()

    # sound generator (mock fallback)
    generator = MockSoundGenerator()

    # v2: ElevenLabs gateway (legacy fallback)
    gateway = _build_gateway(config)
    if gateway:
        engines_list = ", ".join(gateway.keys())
        console.print(f"gateway: elevenlabs ({engines_list})", style="oram.status")
    else:
        console.print("gateway: mock", style="oram.status")

    # v3: engine registry + router
    engine_registry = EngineRegistry.from_config(config)
    engine_router_inst = None
    if engine_registry.available_count > 0:
        engine_router_inst = EngineRouter(
            registry=engine_registry,
            default_provider=config.preferred_provider,
        )
        console.print(
            f"engines: {engine_registry.summary()}",
            style="oram.status",
        )
    else:
        console.print("engines: none registered (mock only)", style="oram.status")

    # v2: usage tracker
    usage_tracker = UsageTracker()

    # LLM adapter (codex default)
    llm_adapter = None
    if config.llm_backend != "none":
        llm = LLMCliAdapter()
        if llm.is_available:
            llm_adapter = llm
            console.print(f"llm: {llm._cli_tool} available", style="oram.status")

    # agent controller
    agent = AgentController(llm_adapter=llm_adapter)

    # TUI
    tui = OramTUI(session, layer_manager, engine)

    # action router
    def on_status(msg: str):
        tui.add_log(msg)
        tui.set_status(msg)

    router = ActionRouter(
        session=session,
        layer_manager=layer_manager,
        engine=engine,
        generator=generator,
        gateway=gateway,
        engine_registry=engine_registry,
        engine_router=engine_router_inst,
        usage_tracker=usage_tracker,
        llm_adapter=llm_adapter,
        config=config,
        session_dir=config.session_dir,
        on_status=on_status,
    )

    # push-to-talk
    ptt = PushToTalk(sample_rate=config.sample_rate)

    # start engine
    engine.start()

    console.print("")
    console.print("oram v2.0.0 — recursive listening instrument", style="oram.title")
    stt_label = "off" if config.no_stt else config.stt_backend
    gw_label = "elevenlabs" if gateway else "mock"
    console.print(
        f"mock: {use_mock}  stt: {stt_label}  gateway: {gw_label}  scene: {session_name}",
        style="oram.mode",
    )
    console.print("")
    console.print(
        "controls: r=record  o=overdub  1-4=select  m=mute  M=solo  x=clear",
        style="oram.status",
    )
    console.print(
        "          l=listen  g=generate  f=fork  tab=mode  s=save  e=export  q=quit",
        style="oram.status",
    )
    console.print(
        "          space=push-to-talk  i=input-mode  d=layer-mode",
        style="oram.status",
    )
    console.print("")

    # main loop with TUI
    try:
        with Live(tui.render(), console=console, refresh_per_second=config.tui_fps,
                   screen=True, transient=True) as live:
            _input_loop(live, tui, router, agent, ptt, stt, engine, session, config)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        console.print("\noram stopped.", style="oram.title")


def _input_loop(live, tui, router, agent, ptt, stt, engine, session, config):
    """main input loop — reads keys and routes actions."""
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        tty.setcbreak(fd)

        while not router.quit_requested:
            # update TUI
            live.update(tui.render())

            # check for key input (non-blocking)
            if select.select([sys.stdin], [], [], 0.05)[0]:
                key = sys.stdin.read(1)

                if key == " ":
                    if session.input_mode == "audio":
                        # direct audio recording
                        action = key_to_action("r", is_recording=engine._recording)
                        if action is not None:
                            router.route(action, raw_text="key:space")
                    else:
                        # push-to-talk prompt toggle
                        was_active = ptt.is_active
                        try:
                            result = ptt.toggle(engine)
                        except RuntimeError as e:
                            tui.set_ptt(False)
                            session.listening = False
                            tui.add_log(str(e))
                            continue
                        if was_active:
                            tui.set_ptt(False)
                            session.listening = False
                            if result is None:
                                tui.add_log("no command audio captured")
                            elif stt is not None:
                                text = stt.transcribe(result, config.sample_rate)
                                if text:
                                    tui.set_current_cmd(text)
                                    action = agent.process_command(text)
                                    router.route(action, raw_text=text)
                            else:
                                tui.add_log("stt unavailable: use keyboard commands")
                        else:
                            tui.set_ptt(True)
                            session.listening = True
                            tui.add_log("listening...")

                elif key == "l":
                    # v2: listen to selected layer
                    route = config.default_listening_route
                    router.route(
                        ListenAction(route=route),
                        raw_text=f"key:l (listen {route})",
                    )

                elif key == "g":
                    # v2: generate from selected layer
                    router.route(
                        GenerateFromAction(
                            route=config.default_listening_route,
                            engine=config.default_engine,
                        ),
                        raw_text="key:g (generate from listening)",
                    )

                elif key == "f":
                    # v2: fork selected layer
                    router.route(ForkLayerAction(), raw_text="key:f (fork)")

                elif key == "d":
                    # v2: cycle layer mode (recorder → looper → sampler)
                    layer = session.layers[session.selected_layer]
                    modes = ["recorder", "looper", "sampler"]
                    current = layer.layer_mode.value
                    next_mode = modes[(modes.index(current) + 1) % len(modes)]
                    router.route(
                        SetLayerModeAction(mode=next_mode),
                        raw_text=f"key:d (mode → {next_mode})",
                    )

                elif key == "i":
                    session.input_mode = "audio" if session.input_mode == "prompt" else "prompt"
                    tui.add_log(f"input mode: {session.input_mode}")

                elif key == "\t":
                    modes = list(type(session.mode))
                    mode_index = modes.index(session.mode)
                    next_mode = modes[(mode_index + 1) % len(modes)]
                    router.route(SetModeAction(mode=next_mode.value), raw_text="key:tab")

                elif key == "\x03":  # ctrl+c
                    break

                elif key == "\x1b":
                    continue

                else:
                    is_recording = engine._recording
                    action = key_to_action(key, is_recording=is_recording)
                    if action is not None:
                        router.route(action, raw_text=f"key:{key}")

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
