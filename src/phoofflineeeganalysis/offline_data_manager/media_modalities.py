"""Modality categories, file extensions, stream-chip mapping, and palette hex constants."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

MEDIA_CATEGORIES: List[str] = [
    "EyeRecordings",
    "WebcamRecordings",
    "ScreenRecordings",
    "Screenshots",
    "AudioRecordings",
]

ALL_MODALITY_KEYS: List[str] = ["EEG"] + list(MEDIA_CATEGORIES)

VIDEO_CATEGORIES: Set[str] = {"EyeRecordings", "WebcamRecordings", "ScreenRecordings"}
SCREENSHOT_CATEGORIES: Set[str] = {"Screenshots"}
AUDIO_CATEGORIES: Set[str] = {"AudioRecordings"}

VIDEO_EXTENSIONS: List[str] = [".mp4", ".avi", ".mov", ".mkv", ".wmv"]
SCREENSHOT_EXTENSIONS: List[str] = [".png", ".jpg", ".jpeg", ".webp", ".bmp"]
AUDIO_EXTENSIONS: List[str] = [".m4a", ".wav", ".mp3", ".aac", ".flac", ".ogg"]
ALL_MEDIA_EXTENSIONS: List[str] = list(VIDEO_EXTENSIONS) + list(SCREENSHOT_EXTENSIONS) + list(AUDIO_EXTENSIONS)

CATEGORY_TO_MEDIA_KIND: Dict[str, str] = {
    "EyeRecordings": "video",
    "WebcamRecordings": "video",
    "ScreenRecordings": "video",
    "Screenshots": "screenshot",
    "AudioRecordings": "audio",
}

MODALITY_DISPLAY_LABELS: Dict[str, str] = {
    "EEG": "EEG Sessions (.xdf)",
    "EyeRecordings": "Eye Camera",
    "WebcamRecordings": "Webcam",
    "ScreenRecordings": "Screen Video",
    "Screenshots": "Screenshots",
    "AudioRecordings": "Audio Recordings",
}

SESSION_STATUS_HEX: Dict[str, str] = {
    "discovered": "#6495ED",
    "computing": "#FFC107",
    "computed": "#28A745",
    "exported": "#007BFF",
    "failed": "#DC3545",
}

MODALITY_CATEGORY_HEX: Dict[str, str] = {
    "EEG": "#6495ED",
    "EyeRecordings": "#9C27B0",
    "WebcamRecordings": "#009688",
    "ScreenRecordings": "#FF6F00",
    "Screenshots": "#607D8B",
    "AudioRecordings": "#9E9D24",
    "default": "#78909C",
}

_CANONICAL_CHIP_ORDER: List[str] = ["EEG", "Motion", "CQ", "Markers"]


def media_kind_for_category(category: Optional[str], extension: Optional[str] = None) -> str:
    """Return ``'video'``, ``'screenshot'``, or ``'audio'`` for *category* (with extension fallback)."""
    cat = str(category or "").strip()
    if cat in CATEGORY_TO_MEDIA_KIND:
        return CATEGORY_TO_MEDIA_KIND[cat]
    low = cat.lower()
    if "screen" in low and "shot" in low:
        return "screenshot"
    if "audio" in low or "voice" in low or "whisper" in low:
        return "audio"
    if extension:
        ext = str(extension).lower()
        if not ext.startswith("."):
            ext = f".{ext}"
        if ext in SCREENSHOT_EXTENSIONS:
            return "screenshot"
        if ext in AUDIO_EXTENSIONS:
            return "audio"
    return "video"


def extensions_for_category(category: Optional[str]) -> List[str]:
    """Return lowercase dotted file extensions scanned for *category*."""
    kind = media_kind_for_category(category)
    if kind == "screenshot":
        return list(SCREENSHOT_EXTENSIONS)
    if kind == "audio":
        return list(AUDIO_EXTENSIONS)
    return list(VIDEO_EXTENSIONS)


def expand_modality_filters(modality_filters: Optional[Iterable[str]]) -> Optional[Set[str]]:
    """Normalize user/UI filter tokens into canonical keys (``EEG`` + category names).

    Returns ``None`` when *modality_filters* is ``None`` (meaning no filter / show everything).
    """
    if modality_filters is None:
        return None
    expanded: Set[str] = set()
    for raw in modality_filters:
        token = str(raw).strip()
        low = token.lower()
        if low in ("eeg", "session", "sessions", "xdf"):
            expanded.add("EEG")
        elif low in ("video", "videos"):
            expanded.update(VIDEO_CATEGORIES)
        elif low in ("screenshot", "screenshots", "image", "images"):
            expanded.update(SCREENSHOT_CATEGORIES)
        elif low in ("audio", "audiorecordings", "voice"):
            expanded.update(AUDIO_CATEGORIES)
        elif low == "eyerecordings" or low == "eye":
            expanded.add("EyeRecordings")
        elif low == "webcamrecordings" or low == "webcam":
            expanded.add("WebcamRecordings")
        elif low == "screenrecordings" or low == "screen":
            expanded.add("ScreenRecordings")
        else:
            expanded.add(token)
    ## END for raw in modality_filters...

    return expanded


def stream_names_to_chips(stream_names: Iterable[str], stream_types: Optional[Iterable[str]] = None) -> List[str]:
    """Map raw XDF stream names/types onto compact ordered modality chips."""
    found: Set[str] = set()
    extras: List[str] = []
    for name in stream_names:
        text = str(name or "").strip()
        if not text:
            continue
        low = text.lower()
        if text == "Epoc X" or ("eeg" in low and "quality" not in low and "equality" not in low):
            found.add("EEG")
        elif "equality" in low or "quality" in low or low in ("cq", "eegq"):
            found.add("CQ")
        elif "motion" in low or "gyro" in low or "imu" in low:
            found.add("Motion")
        elif any(marker_tok in low for marker_tok in ("logger", "event", "marker", "seqdebug", "whisper", "annotation")):
            found.add("Markers")
        elif low == "epoc x":
            found.add("EEG")
        else:
            if text not in extras:
                extras.append(text)
    ## END for name in stream_names...

    if stream_types is not None:
        for stype in stream_types:
            t_low = str(stype or "").strip().lower()
            if not t_low:
                continue
            if t_low == "eeg":
                found.add("EEG")
            elif t_low in ("cq", "quality"):
                found.add("CQ")
            elif t_low in ("motion", "acc", "gyro", "quat", "mag"):
                found.add("Motion")
            elif t_low in ("markers", "string", "events"):
                found.add("Markers")
        ## END for stype in stream_types...

    ordered = [chip for chip in _CANONICAL_CHIP_ORDER if chip in found]
    for extra in extras:
        if extra not in ordered:
            ordered.append(extra)
    ## END for extra in extras...

    return ordered


__all__ = [
    "MEDIA_CATEGORIES",
    "ALL_MODALITY_KEYS",
    "VIDEO_CATEGORIES",
    "SCREENSHOT_CATEGORIES",
    "AUDIO_CATEGORIES",
    "VIDEO_EXTENSIONS",
    "SCREENSHOT_EXTENSIONS",
    "AUDIO_EXTENSIONS",
    "ALL_MEDIA_EXTENSIONS",
    "CATEGORY_TO_MEDIA_KIND",
    "MODALITY_DISPLAY_LABELS",
    "SESSION_STATUS_HEX",
    "MODALITY_CATEGORY_HEX",
    "media_kind_for_category",
    "extensions_for_category",
    "expand_modality_filters",
    "stream_names_to_chips",
]
