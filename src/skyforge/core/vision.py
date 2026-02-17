"""AI vision analysis — LLM-powered aerial image interpretation."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Analysis profiles
# ---------------------------------------------------------------------------

ANALYSIS_PROFILES: dict[str, dict[str, Any]] = {
    "general": {
        "name": "General Aerial Survey",
        "prompt": (
            "Analyze this aerial/drone image. Describe what you see, identify any notable"
            " features, structures, terrain, vegetation, or objects. Note any potential"
            " issues or points of interest."
        ),
        "categories": [
            "terrain",
            "structure",
            "vegetation",
            "water",
            "road",
            "vehicle",
            "person",
            "other",
        ],
    },
    "infrastructure": {
        "name": "Infrastructure Inspection",
        "prompt": (
            "Inspect this aerial image for infrastructure condition. Identify any visible"
            " damage, wear, corrosion, cracks, or structural issues. Rate severity of any"
            " findings."
        ),
        "categories": [
            "crack",
            "corrosion",
            "damage",
            "wear",
            "deformation",
            "vegetation_intrusion",
            "water_damage",
        ],
    },
    "construction": {
        "name": "Construction Progress",
        "prompt": (
            "Assess construction progress in this aerial image. Identify active work areas,"
            " equipment, materials, completed sections, and any safety concerns."
        ),
        "categories": [
            "foundation",
            "framing",
            "roofing",
            "equipment",
            "materials",
            "safety_concern",
            "completed",
        ],
    },
    "agricultural": {
        "name": "Agricultural Analysis",
        "prompt": (
            "Analyze this aerial image of agricultural land. Assess crop health, irrigation"
            " status, identify any pest damage, disease signs, or areas needing attention."
        ),
        "categories": [
            "healthy_crop",
            "stressed_crop",
            "bare_soil",
            "irrigation",
            "pest_damage",
            "weed",
            "equipment",
        ],
    },
    "roof": {
        "name": "Roof Inspection",
        "prompt": (
            "Inspect this aerial view of a roof. Identify any damage, missing shingles,"
            " ponding water, debris, flashing issues, or areas requiring maintenance."
        ),
        "categories": [
            "missing_shingles",
            "crack",
            "ponding",
            "debris",
            "flashing",
            "moss_algae",
            "structural",
        ],
    },
    "solar": {
        "name": "Solar Panel Inspection",
        "prompt": (
            "Inspect this aerial view of solar panels. Identify any damaged panels,"
            " hotspots, soiling, shading issues, wiring problems, or maintenance needs."
        ),
        "categories": [
            "damaged_panel",
            "hotspot",
            "soiling",
            "shading",
            "wiring",
            "debris",
            "degradation",
        ],
    },
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class VisionFinding:
    """A single finding from AI vision analysis."""

    category: str
    description: str
    severity: str  # "info", "low", "medium", "high", "critical"
    confidence: float
    location_hint: str  # "center", "top-left", "bottom-right", etc.


@dataclass
class FrameVisionAnalysis:
    """Vision analysis results for a single video frame."""

    frame_idx: int
    timestamp_s: float
    findings: list[VisionFinding]
    raw_response: str


@dataclass
class VideoVisionReport:
    """Complete AI vision report for a video file."""

    source: str
    profile: str
    provider: str
    total_frames_analyzed: int
    frames: list[FrameVisionAnalysis] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)  # severity -> count

    def to_dict(self) -> dict:
        """Serialize report to a JSON-compatible dict."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Frame encoding
# ---------------------------------------------------------------------------


def _encode_frame_jpeg(frame: np.ndarray, quality: int = 85) -> str:
    """Encode an OpenCV frame as a base64 JPEG string.

    Args:
        frame: BGR image array from cv2.
        quality: JPEG quality 0-100.

    Returns:
        Base64-encoded JPEG string.
    """
    encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
    ok, buf = cv2.imencode(".jpg", frame, encode_params)
    if not ok:
        msg = "Failed to encode frame as JPEG"
        raise RuntimeError(msg)
    return base64.b64encode(buf.tobytes()).decode("utf-8")


# ---------------------------------------------------------------------------
# LLM provider calls (lazy imports)
# ---------------------------------------------------------------------------

_JSON_RESPONSE_INSTRUCTIONS = (
    "\n\nRespond ONLY with a JSON array of findings. Each finding must have exactly these"
    " fields:\n"
    '  {"category": "<string>", "description": "<string>",'
    ' "severity": "info|low|medium|high|critical",'
    ' "confidence": <0.0-1.0>, "location_hint": "<string>"}\n'
    "If there are no notable findings, return an empty array: []"
)


def _call_claude(image_b64: str, prompt: str, api_key: str) -> str:
    """Send an image to Claude vision API and return the response text.

    Lazy-imports the anthropic package so it is only required at call time.
    """
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ],
    )
    return message.content[0].text


def _call_openai(image_b64: str, prompt: str, api_key: str) -> str:
    """Send an image to OpenAI GPT-4o vision API and return the response text.

    Lazy-imports the openai package so it is only required at call time.
    """
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}",
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ],
    )
    return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_vision_response(response: str, profile: dict[str, Any]) -> list[VisionFinding]:
    """Parse an LLM vision response into structured findings.

    Attempts JSON parsing first. Falls back to wrapping the raw text as a
    single "info" finding if the response is not valid JSON.
    """
    valid_categories = set(profile.get("categories", []))
    valid_severities = {"info", "low", "medium", "high", "critical"}

    # Try to extract JSON from the response (may be wrapped in markdown fences)
    text = response.strip()
    if text.startswith("```"):
        # Strip markdown code fences
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            parsed = [parsed]
    except json.JSONDecodeError:
        # Fallback: wrap raw text as a single finding
        return [
            VisionFinding(
                category="other",
                description=response.strip()[:500],
                severity="info",
                confidence=0.5,
                location_hint="general",
            )
        ]

    findings: list[VisionFinding] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue

        category = str(item.get("category", "other")).lower()
        if category not in valid_categories:
            category = "other" if "other" in valid_categories else category

        severity = str(item.get("severity", "info")).lower()
        if severity not in valid_severities:
            severity = "info"

        try:
            confidence = float(item.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.5

        findings.append(
            VisionFinding(
                category=category,
                description=str(item.get("description", ""))[:500],
                severity=severity,
                confidence=round(confidence, 3),
                location_hint=str(item.get("location_hint", "general")),
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_frame(
    frame: np.ndarray,
    profile: str = "general",
    provider: str = "claude",
    api_key: str | None = None,
) -> list[VisionFinding]:
    """Analyze a single frame with an AI vision model.

    Args:
        frame: BGR image array from OpenCV.
        profile: Analysis profile key (see ANALYSIS_PROFILES).
        provider: LLM provider — "claude" or "openai".
        api_key: API key override. Falls back to ANTHROPIC_API_KEY or
                 OPENAI_API_KEY environment variables.

    Returns:
        List of VisionFinding objects extracted from the LLM response.

    Raises:
        ValueError: If profile or provider is unknown, or no API key found.
        ImportError: If the required provider SDK is not installed.
    """
    if profile not in ANALYSIS_PROFILES:
        msg = f"Unknown profile '{profile}'. Available: {', '.join(ANALYSIS_PROFILES)}"
        raise ValueError(msg)

    profile_cfg = ANALYSIS_PROFILES[profile]

    # Build the full prompt with JSON response instructions
    full_prompt = profile_cfg["prompt"] + _JSON_RESPONSE_INSTRUCTIONS
    categories_hint = ", ".join(profile_cfg["categories"])
    full_prompt += f"\n\nUse these categories where applicable: {categories_hint}"

    # Resolve API key
    if provider == "claude":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    elif provider == "openai":
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
    else:
        msg = f"Unknown provider '{provider}'. Use 'claude' or 'openai'."
        raise ValueError(msg)

    if not key:
        env_var = "ANTHROPIC_API_KEY" if provider == "claude" else "OPENAI_API_KEY"
        msg = f"No API key provided. Set {env_var} or pass api_key parameter."
        raise ValueError(msg)

    # Encode frame
    image_b64 = _encode_frame_jpeg(frame)

    # Call LLM
    if provider == "claude":
        raw_response = _call_claude(image_b64, full_prompt, key)
    else:
        raw_response = _call_openai(image_b64, full_prompt, key)

    return _parse_vision_response(raw_response, profile_cfg)


def analyze_video(
    video_path: Path,
    profile: str = "general",
    provider: str = "claude",
    sample_interval: float = 5.0,
    api_key: str | None = None,
    max_frames: int = 20,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> VideoVisionReport:
    """Analyze sampled video frames with an AI vision API.

    Args:
        video_path: Path to the video file.
        profile: Analysis profile key.
        provider: LLM provider — "claude" or "openai".
        sample_interval: Seconds between sampled frames.
        api_key: API key override.
        max_frames: Maximum number of frames to analyze.
        on_progress: Optional callback (current_frame, total_frames, findings_count).

    Returns:
        VideoVisionReport with per-frame findings and severity summary.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        msg = f"Cannot open video: {video_path}"
        raise RuntimeError(msg)

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_video_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Calculate which frames to sample
    frame_interval = int(fps * sample_interval)
    if frame_interval < 1:
        frame_interval = 1

    sample_indices: list[int] = []
    idx = 0
    while idx < total_video_frames:
        sample_indices.append(idx)
        idx += frame_interval
    if len(sample_indices) > max_frames:
        # Evenly subsample to stay within budget
        step = len(sample_indices) / max_frames
        sample_indices = [sample_indices[int(i * step)] for i in range(max_frames)]

    total_to_analyze = len(sample_indices)
    frame_analyses: list[FrameVisionAnalysis] = []
    severity_counts: dict[str, int] = {}
    total_findings = 0

    for i, frame_idx in enumerate(sample_indices):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            continue

        timestamp_s = frame_idx / fps

        try:
            findings = analyze_frame(frame, profile=profile, provider=provider, api_key=api_key)
        except (ValueError, RuntimeError) as exc:
            # Store the error as a single finding so processing continues
            findings = [
                VisionFinding(
                    category="other",
                    description=f"Analysis error: {exc}",
                    severity="info",
                    confidence=0.0,
                    location_hint="general",
                )
            ]

        # Build raw response placeholder (actual raw is inside analyze_frame)
        raw = json.dumps([asdict(f) for f in findings], indent=2)

        frame_analyses.append(
            FrameVisionAnalysis(
                frame_idx=frame_idx,
                timestamp_s=round(timestamp_s, 2),
                findings=findings,
                raw_response=raw,
            )
        )

        for f in findings:
            severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1
            total_findings += 1

        if on_progress:
            on_progress(i + 1, total_to_analyze, total_findings)

    cap.release()

    return VideoVisionReport(
        source=str(video_path),
        profile=profile,
        provider=provider,
        total_frames_analyzed=len(frame_analyses),
        frames=frame_analyses,
        summary=severity_counts,
    )


def save_vision_report(report: VideoVisionReport, output: Path) -> None:
    """Persist a vision report as JSON."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.to_dict(), indent=2))


def estimate_cost(
    video_path: Path,
    sample_interval: float = 5.0,
    max_frames: int = 20,
    provider: str = "claude",
) -> dict[str, float | int | str]:
    """Estimate API cost for analyzing a video without making any calls.

    Args:
        video_path: Path to the video file.
        sample_interval: Seconds between sampled frames.
        max_frames: Maximum frames to analyze.
        provider: LLM provider name.

    Returns:
        Dict with frame_count, duration_s, estimated_cost_usd, and provider.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return {
            "frame_count": 0,
            "duration_s": 0.0,
            "estimated_cost_usd": 0.0,
            "provider": provider,
        }

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps if fps > 0 else 0.0
    cap.release()

    frame_interval = int(fps * sample_interval)
    if frame_interval < 1:
        frame_interval = 1

    sample_count = 0
    idx = 0
    while idx < total_frames:
        sample_count += 1
        idx += frame_interval

    frame_count = min(sample_count, max_frames)

    # Rough cost estimates per image (input token cost for ~200KB JPEG)
    cost_per_image = {"claude": 0.01, "openai": 0.01}
    rate = cost_per_image.get(provider, 0.01)

    return {
        "frame_count": frame_count,
        "duration_s": round(duration, 2),
        "estimated_cost_usd": round(frame_count * rate, 3),
        "provider": provider,
    }
