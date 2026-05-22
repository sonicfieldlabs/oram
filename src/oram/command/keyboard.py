"""oram.command.keyboard — keyboard event handling and routing.

maps key events to structured actions.
"""

from __future__ import annotations

from oram.command.schemas import (
    AnalyzeMixAction,
    ClearLayerAction,
    ExportMixAction,
    MuteLayerAction,
    OramAction,
    OverdubAction,
    QuitAction,
    RecordAction,
    SaveSessionAction,
    SelectLayerAction,
    SoloLayerAction,
    StopRecordingAction,
)

# key -> action mapping
KEY_MAP: dict[str, OramAction] = {
    "r": RecordAction(),
    "o": OverdubAction(),
    "1": SelectLayerAction(target=1),
    "2": SelectLayerAction(target=2),
    "3": SelectLayerAction(target=3),
    "4": SelectLayerAction(target=4),
    "m": MuteLayerAction(),
    "M": SoloLayerAction(),
    "x": ClearLayerAction(),
    "s": SaveSessionAction(),
    "e": ExportMixAction(),
    "a": AnalyzeMixAction(),
    "q": QuitAction(),
}


def key_to_action(key: str, is_recording: bool = False) -> OramAction | None:
    """convert a key press to a structured action.

    returns None if the key doesn't map to an action.
    """
    # special case: 'r' during recording means stop
    if key == "r" and is_recording:
        return StopRecordingAction()

    return KEY_MAP.get(key)
