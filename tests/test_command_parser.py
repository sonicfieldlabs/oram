"""tests for the command parser — every command category from dev_plan section 9."""

from __future__ import annotations

from oram.command.parser import CommandParser
from oram.command.schemas import (
    AnalyzeMixAction,
    ApplyEffectAction,
    ClearLayerAction,
    ExportMixAction,
    GenerateLayerAction,
    KillAudioAction,
    MuteLayerAction,
    OverdubAction,
    QuitAction,
    RecordAction,
    SaveSessionAction,
    SelectLayerAction,
    SetModeAction,
    SetPanAction,
    SetVolumeAction,
    SoloLayerAction,
    StopRecordingAction,
    UnknownAction,
)

parser = CommandParser()


# --- transport commands ---


class TestTransportCommands:
    def test_record_eight_seconds(self):
        result = parser.parse("record eight seconds")
        assert isinstance(result, RecordAction)
        assert result.duration == 8.0

    def test_record_numeric(self):
        result = parser.parse("record 8 seconds")
        assert isinstance(result, RecordAction)
        assert result.duration == 8.0

    def test_record_bars(self):
        result = parser.parse("record four bars")
        assert isinstance(result, RecordAction)
        assert result.bars == 4

    def test_record_layer_target(self):
        result = parser.parse("record layer two eight seconds")
        assert isinstance(result, RecordAction)
        assert result.target == 2
        assert result.duration == 8.0

    def test_stop_recording(self):
        result = parser.parse("stop recording")
        assert isinstance(result, StopRecordingAction)

    def test_kill_audio(self):
        result = parser.parse("kill audio")
        assert isinstance(result, KillAudioAction)

    def test_loop_this(self):
        result = parser.parse("loop this")
        assert isinstance(result, SetModeAction)
        assert result.mode == "loop"

    def test_overdub(self):
        result = parser.parse("overdub")
        assert isinstance(result, OverdubAction)

    def test_mute_layer_two(self):
        result = parser.parse("mute layer two")
        assert isinstance(result, MuteLayerAction)
        assert result.target == 2

    def test_mute_layer_numeric(self):
        result = parser.parse("mute layer 2")
        assert isinstance(result, MuteLayerAction)
        assert result.target == 2

    def test_solo_layer_one(self):
        result = parser.parse("solo layer one")
        assert isinstance(result, SoloLayerAction)
        assert result.target == 1

    def test_clear_layer_one(self):
        result = parser.parse("clear layer one")
        assert isinstance(result, ClearLayerAction)
        assert result.target == 1

    def test_select_layer_three(self):
        result = parser.parse("select layer three")
        assert isinstance(result, SelectLayerAction)
        assert result.target == 3

    def test_save_scene(self):
        result = parser.parse("save scene")
        assert isinstance(result, SaveSessionAction)

    def test_export_mix(self):
        result = parser.parse("export mix")
        assert isinstance(result, ExportMixAction)

    def test_quit(self):
        result = parser.parse("quit")
        assert isinstance(result, QuitAction)


# --- transform commands ---


class TestTransformCommands:
    def test_reverse_layer_one(self):
        result = parser.parse("reverse layer one")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "reverse"
        assert result.target == 1

    def test_make_slower(self):
        result = parser.parse("make layer one slower")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "speed"
        assert result.parameters.speed == 0.5

    def test_make_it_slower(self):
        result = parser.parse("make it slower")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "speed"
        assert result.target == "selected"

    def test_pitch_it_down(self):
        result = parser.parse("pitch it down")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "pitch"
        assert result.parameters.semitones is not None
        assert result.parameters.semitones < 0

    def test_pitch_up_semitones(self):
        result = parser.parse("pitch layer two up five semitones")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "pitch"
        assert result.target == 2
        assert result.parameters.semitones == 5.0

    def test_fade_the_end(self):
        result = parser.parse("fade the end")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "fade_out"

    def test_trim_the_beginning(self):
        result = parser.parse("trim the beginning")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "trim_start"

    def test_filter_the_voice(self):
        result = parser.parse("filter the voice")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "lowpass"

    def test_make_it_darker(self):
        result = parser.parse("make it darker")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "lowpass"

    def test_make_it_thinner(self):
        result = parser.parse("make it thinner")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "highpass"

    def test_granulate_softly(self):
        result = parser.parse("granulate softly")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "granular"
        assert result.parameters.density is not None
        assert result.parameters.density <= 0.4

    def test_stretch_breathe(self):
        result = parser.parse("stretch it until it breathes")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "stretch_breathe"


class TestMixCommands:
    def test_set_volume_layer(self):
        result = parser.parse("set volume layer 1 0.50")
        assert isinstance(result, SetVolumeAction)
        assert result.target == 1
        assert result.volume == 0.5

    def test_set_volume_preserves_quiet_and_mute(self):
        quiet = parser.parse("set volume layer 1 0.009")
        assert isinstance(quiet, SetVolumeAction)
        assert quiet.target == 1
        assert quiet.volume == 0.009

        mute = parser.parse("set volume layer 1 0")
        assert isinstance(mute, SetVolumeAction)
        assert mute.target == 1
        assert mute.volume == 0

    def test_set_volume_percent(self):
        result = parser.parse("volume layer two 50")
        assert isinstance(result, SetVolumeAction)
        assert result.target == 2
        assert result.volume == 0.5

    def test_set_pan_layer(self):
        result = parser.parse("set pan layer 3 -0.75")
        assert isinstance(result, SetPanAction)
        assert result.target == 3
        assert result.pan == -0.75


# --- spatial commands ---


class TestSpatialCommands:
    def test_far_away(self):
        result = parser.parse("move it far away")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "spatial_far"

    def test_small_room(self):
        result = parser.parse("turn this into a small room")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "reverb"
        assert result.parameters.decay == "short"

    def test_wash_reverb(self):
        result = parser.parse("wash it in reverb")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "reverb"
        assert result.parameters.wet is not None
        assert result.parameters.wet >= 0.7

    def test_add_distance(self):
        result = parser.parse("add more distance")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "spatial_far"


# --- generative commands ---


class TestGenerativeCommands:
    def test_distant_metallic_rain(self):
        result = parser.parse("add distant metallic rain")
        assert isinstance(result, GenerateLayerAction)
        assert "rain" in result.prompt

    def test_room_tone(self):
        result = parser.parse("create a room tone under this loop")
        assert isinstance(result, GenerateLayerAction)
        assert "room tone" in result.prompt

    def test_synthetic_forest(self):
        result = parser.parse("summon a synthetic forest")
        assert isinstance(result, GenerateLayerAction)
        assert "forest" in result.prompt

    def test_low_drone(self):
        result = parser.parse("add a low unstable drone")
        assert isinstance(result, GenerateLayerAction)
        assert "drone" in result.prompt

    def test_fake_field_recording(self):
        result = parser.parse("make a fake field recording")
        assert isinstance(result, GenerateLayerAction)
        assert "recording" in result.prompt

    def test_quiet_machine(self):
        result = parser.parse("generate a quiet machine ambience")
        assert isinstance(result, GenerateLayerAction)
        assert "machine" in result.prompt


# --- listening commands ---


class TestListeningCommands:
    def test_listen_texture(self):
        result = parser.parse("listen to the texture")
        assert isinstance(result, AnalyzeMixAction)

    def test_describe_mix(self):
        result = parser.parse("describe the current mix")
        assert isinstance(result, AnalyzeMixAction)

    def test_what_is_dense(self):
        result = parser.parse("what is too dense")
        assert isinstance(result, AnalyzeMixAction)
        assert result.focus == "density"

    def test_what_changed(self):
        result = parser.parse("what changed in layer two")
        assert isinstance(result, AnalyzeMixAction)
        assert result.target == 2

    def test_find_speech(self):
        result = parser.parse("find speech residue")
        assert isinstance(result, AnalyzeMixAction)
        assert result.focus == "speech"

    def test_analyze_loop(self):
        result = parser.parse("analyze the loop")
        assert isinstance(result, AnalyzeMixAction)


# --- rejection / safety ---


class TestRejection:
    def test_empty_command(self):
        result = parser.parse("")
        assert isinstance(result, UnknownAction)

    def test_whitespace_only(self):
        result = parser.parse("   ")
        assert isinstance(result, UnknownAction)

    def test_nonsense(self):
        result = parser.parse("make the loop impossible")
        assert isinstance(result, UnknownAction)

    def test_gibberish(self):
        result = parser.parse("xyzzy plugh")
        assert isinstance(result, UnknownAction)

    def test_invalid_select_layer_rejected_without_exception(self):
        result = parser.parse("select layer 9")
        assert isinstance(result, UnknownAction)
        assert "invalid command" in result.reason

    def test_punctuation_stripped(self):
        # should still parse after punctuation stripping
        result = parser.parse("record 8 seconds!!!")
        assert isinstance(result, RecordAction)
        assert result.duration == 8.0

    def test_case_insensitive(self):
        result = parser.parse("REVERSE LAYER ONE")
        assert isinstance(result, ApplyEffectAction)
        assert result.effect == "reverse"


class TestNewEffectCommands:
    """v0.4: the full fx vocabulary — delay/chorus/flanger/phaser/distortion/
    bitcrush/stutter/normalize/bandpass/spatial near+wide — plus undo,
    regenerate, listen routes, and volume deltas."""

    def _effect(self, text: str) -> ApplyEffectAction:
        result = parser.parse(text)
        assert isinstance(result, ApplyEffectAction), f"{text!r} -> {result}"
        return result

    def test_delay_variants(self):
        for text in ("delay", "add delay", "echo it", "delay layer 2"):
            assert self._effect(text).effect == "delay"

    def test_pingpong_delay(self):
        action = self._effect("ping pong delay")
        assert action.effect == "delay"
        assert action.parameters.pingpong is True

    def test_chorus(self):
        assert self._effect("chorus").effect == "chorus"
        assert self._effect("add some chorus").effect == "chorus"

    def test_chorus_of_birds_is_generative(self):
        result = parser.parse("a chorus of birds singing")
        assert isinstance(result, GenerateLayerAction)

    def test_flanger_phaser(self):
        assert self._effect("flanger").effect == "flanger"
        assert self._effect("phaser layer 3").effect == "phaser"

    def test_distortion(self):
        assert self._effect("distort it").effect == "distortion"
        warm = self._effect("warm distortion")
        assert warm.parameters.character == "warm"
        fuzz = self._effect("add fuzz")
        assert fuzz.parameters.character == "fuzz"

    def test_bitcrush(self):
        action = self._effect("bitcrush to 6 bits")
        assert action.effect == "bitcrush"
        assert action.parameters.bits == 6

    def test_stutter_glitch(self):
        assert self._effect("stutter").effect == "stutter"
        assert self._effect("glitch it").effect == "stutter"

    def test_normalize(self):
        assert self._effect("normalize").effect == "normalize"
        assert self._effect("normalise the mix").effect == "normalize"

    def test_filter_with_frequency(self):
        action = self._effect("filter layer 2 lowpass 800")
        assert action.effect == "lowpass"
        assert action.parameters.cutoff_hz == 800.0
        assert action.target == 2

    def test_bandpass_with_q(self):
        action = self._effect("bandpass 1200 q 3")
        assert action.effect == "bandpass"
        assert action.parameters.cutoff_hz == 1200.0
        assert action.parameters.q == 3.0

    def test_speed_ratio(self):
        action = self._effect("set speed 1.5")
        assert action.effect == "speed"
        assert action.parameters.speed == 1.5

    def test_spatial_family(self):
        assert self._effect("make it wider").effect == "spatial_wide"
        assert self._effect("bring it closer").effect == "spatial_near"
        assert self._effect("make it far away").effect == "spatial_far"

    def test_descriptive_delay_text_still_generates(self):
        result = parser.parse("add a delayed choir texture with rain")
        assert isinstance(result, GenerateLayerAction)


class TestNewSessionCommands:
    def test_bare_listen(self):
        from oram.command.schemas import ListenAction

        result = parser.parse("listen")
        assert isinstance(result, ListenAction)

    def test_listen_route_alias(self):
        from oram.command.schemas import ListenAction

        result = parser.parse("listen via spectral")
        assert isinstance(result, ListenAction)
        assert result.route == "technical"

    def test_listen_mix_is_analyze(self):
        from oram.command.schemas import AnalyzeMixAction

        result = parser.parse("listen to the mix")
        assert isinstance(result, AnalyzeMixAction)

    def test_mode_command(self):
        from oram.command.schemas import SetModeAction

        result = parser.parse("mode loop")
        assert isinstance(result, SetModeAction)
        assert result.mode == "loop"

    def test_regenerate(self):
        from oram.command.schemas import RegenerateAction

        result = parser.parse("regenerate")
        assert isinstance(result, RegenerateAction)

    def test_name_session(self):
        from oram.command.schemas import NameSessionAction

        result = parser.parse("name session grey chapel")
        assert isinstance(result, NameSessionAction)
        assert result.name == "grey chapel"

    def test_undo(self):
        from oram.command.schemas import RemoveEffectAction

        result = parser.parse("undo")
        assert isinstance(result, RemoveEffectAction)
        assert result.effect == "last"

    def test_unmute_forces_state(self):
        result = parser.parse("unmute layer 2")
        assert isinstance(result, MuteLayerAction)
        assert result.state is False

    def test_volume_half_is_half(self):
        result = parser.parse("set volume half")
        assert isinstance(result, SetVolumeAction)
        assert result.volume == 0.5

    def test_softer_is_volume_delta(self):
        result = parser.parse("make everything softer")
        assert isinstance(result, SetVolumeAction)
        assert result.target == "all"
        assert result.delta is not None and result.delta < 0

    def test_louder(self):
        result = parser.parse("louder")
        assert isinstance(result, SetVolumeAction)
        assert result.delta is not None and result.delta > 0
