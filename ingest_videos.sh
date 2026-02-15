#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Skyforge Ingest Pipeline
# AI Aerial Solutions — Horizon by Yosemite / 1st Flight
#
# Normalizes mixed-source aerial footage to a single edit-friendly baseline:
#   - Constant frame rate (30fps)
#   - H.264 yuv420p (max compatibility)
#   - HDR → SDR tonemap for iPhone HLG footage
#   - Audio normalization (loudnorm)
#   - 1080p proxy generation for smooth editing
#
# Usage:
#   ./ingest_videos.sh [--skip-proxies] [--skip-drone] [--dry-run]
#
# Sources:
#   Drone (ATOM_001): 4K H.264 SDR CFR ~30fps, no audio, has SRT telemetry
#   iPhone (IMG_*):   1080x1920 HEVC 10-bit HLG HDR VFR → needs tonemap + CFR
#   Meta Glasses:     1504x2000 HEVC SDR VFR → needs CFR
# ============================================================================

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
RAW_DIR="$PROJECT_DIR/01_RAW"
NORM_DIR="$PROJECT_DIR/02_NORMALIZED"
PROXY_DIR="$PROJECT_DIR/02_PROXIES"

SKIP_PROXIES=false
SKIP_DRONE=false
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --skip-proxies) SKIP_PROXIES=true ;;
    --skip-drone)   SKIP_DRONE=true ;;
    --dry-run)      DRY_RUN=true ;;
    *)              echo "Unknown option: $arg"; exit 1 ;;
  esac
done

# Timeline settings
TARGET_FPS="30"
PROXY_SCALE="1920:-2"

# Audio normalization
AUDIO_FILTER="loudnorm=I=-16:TP=-1.5:LRA=11"

# H.264 encode — edit-friendly with frequent keyframes for smooth scrubbing
ENC_COMMON=(-c:v libx264 -pix_fmt yuv420p -preset veryfast -crf 18 \
  -x264-params keyint=60:min-keyint=60:scenecut=0)

# CFR flags (fixes VFR from iPhone/Meta)
CFR_FLAGS=(-fps_mode cfr -r "$TARGET_FPS")

# HDR HLG → SDR tonemap (for iPhone HLG footage)
TONEMAP_FILTER="zscale=t=linear:npl=100,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"

# ============================================================================
# Helpers
# ============================================================================

is_hdr() {
  local f="$1"
  local trc
  trc="$(ffprobe -v error -select_streams v:0 \
    -show_entries stream=color_transfer \
    -of default=nw=1:nk=1 "$f" 2>/dev/null || true)"
  [[ "$trc" == "smpte2084" || "$trc" == "arib-std-b67" ]]
}

has_audio() {
  local f="$1"
  local count
  count="$(ffprobe -v error -select_streams a \
    -show_entries stream=codec_type \
    -of csv=p=0 "$f" 2>/dev/null | wc -l || echo 0)"
  [[ "$count" -gt 0 ]]
}

file_size_mb() {
  local f="$1"
  local bytes
  bytes="$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo 0)"
  echo "scale=1; $bytes / 1048576" | bc
}

log() {
  echo "[$(date '+%H:%M:%S')] $*"
}

# ============================================================================
# Processing functions
# ============================================================================

normalize_video() {
  local in="$1"
  local out="$2"
  local device="$3"

  if [[ -f "$out" ]]; then
    log "SKIP (exists): $(basename "$out")"
    return
  fi

  local size_mb
  size_mb="$(file_size_mb "$in")"
  log "NORMALIZE: $(basename "$in") (${size_mb}MB) [$device]"

  if $DRY_RUN; then
    log "  DRY RUN — would write: $out"
    return
  fi

  local audio_opts=()
  if has_audio "$in"; then
    audio_opts=(-c:a aac -b:a 256k -af "$AUDIO_FILTER")
  else
    audio_opts=(-an)
  fi

  if is_hdr "$in"; then
    log "  HDR detected → tonemapping to SDR"
    ffmpeg -hide_banner -y -i "$in" \
      -vf "$TONEMAP_FILTER" \
      "${CFR_FLAGS[@]}" \
      "${ENC_COMMON[@]}" \
      "${audio_opts[@]}" \
      -movflags +faststart \
      "$out" 2>&1 | tail -1
  else
    ffmpeg -hide_banner -y -i "$in" \
      "${CFR_FLAGS[@]}" \
      "${ENC_COMMON[@]}" \
      "${audio_opts[@]}" \
      -movflags +faststart \
      "$out" 2>&1 | tail -1
  fi

  log "  → $(basename "$out") ($(file_size_mb "$out")MB)"
}

generate_proxy() {
  local in="$1"
  local out="$2"

  if [[ -f "$out" ]]; then
    log "SKIP proxy (exists): $(basename "$out")"
    return
  fi

  if $DRY_RUN; then
    log "  DRY RUN proxy — would write: $out"
    return
  fi

  ffmpeg -hide_banner -y -i "$in" \
    -vf "scale=$PROXY_SCALE" \
    -c:v libx264 -pix_fmt yuv420p -preset veryfast -crf 28 \
    -c:a aac -b:a 128k \
    -movflags +faststart \
    "$out" 2>&1 | tail -1

  log "  PROXY → $(basename "$out") ($(file_size_mb "$out")MB)"
}

# ============================================================================
# Main
# ============================================================================

mkdir -p "$NORM_DIR/Drone" "$NORM_DIR/iPhone" "$NORM_DIR/Meta_Glasses"
mkdir -p "$PROXY_DIR/Drone" "$PROXY_DIR/iPhone" "$PROXY_DIR/Meta_Glasses"

log "================================================"
log "Skyforge Ingest Pipeline"
log "================================================"
log "RAW:        $RAW_DIR"
log "NORMALIZED: $NORM_DIR"
log "PROXIES:    $PROXY_DIR"
log "Target FPS: $TARGET_FPS"
log "Proxy res:  $PROXY_SCALE"
$DRY_RUN && log "MODE: DRY RUN"
echo ""

# --- Drone footage ---
if ! $SKIP_DRONE; then
  log "--- Processing Drone footage ---"
  for f in "$RAW_DIR"/Drone/*.MP4; do
    [[ -f "$f" ]] || continue
    base="$(basename "$f" .MP4)"
    normalize_video "$f" "$NORM_DIR/Drone/${base}_norm.mp4" "drone"
    if ! $SKIP_PROXIES; then
      generate_proxy "$NORM_DIR/Drone/${base}_norm.mp4" "$PROXY_DIR/Drone/${base}_proxy.mp4"
    fi
  done
  # Copy SRT telemetry files alongside
  for srt in "$RAW_DIR"/Drone/*.SRT; do
    [[ -f "$srt" ]] || continue
    cp -n "$srt" "$NORM_DIR/Drone/" 2>/dev/null || true
  done
  echo ""
else
  log "--- Skipping drone footage ---"
fi

# --- iPhone footage (native HDR) ---
log "--- Processing iPhone footage ---"
for f in "$RAW_DIR"/iPhone/IMG_*.MOV; do
  [[ -f "$f" ]] || continue
  base="$(basename "$f" .MOV)"
  normalize_video "$f" "$NORM_DIR/iPhone/${base}_norm.mp4" "iphone"
  if ! $SKIP_PROXIES; then
    generate_proxy "$NORM_DIR/iPhone/${base}_norm.mp4" "$PROXY_DIR/iPhone/${base}_proxy.mp4"
  fi
done
echo ""

# --- Meta Glasses footage ---
log "--- Processing Meta Glasses footage ---"
for f in "$RAW_DIR"/Meta_Glasses/IMG_*.MOV; do
  [[ -f "$f" ]] || continue
  base="$(basename "$f" .MOV)"
  normalize_video "$f" "$NORM_DIR/Meta_Glasses/${base}_norm.mp4" "meta_glasses"
  if ! $SKIP_PROXIES; then
    generate_proxy "$NORM_DIR/Meta_Glasses/${base}_norm.mp4" "$PROXY_DIR/Meta_Glasses/${base}_proxy.mp4"
  fi
done
echo ""

# --- Skip iPhone singular_display duplicates (same as Meta Glasses) ---
log "NOTE: Skipping iPhone singular_display files (duplicates of Meta Glasses footage)"
echo ""

# --- Generate manifest ---
MANIFEST="$PROJECT_DIR/manifest.json"
log "Generating manifest: $MANIFEST"

if ! $DRY_RUN; then
  echo "[" > "$MANIFEST"
  first=true
  for norm in "$NORM_DIR"/*/*.mp4 "$NORM_DIR"/*/*/*.mp4 2>/dev/null; do
    [[ -f "$norm" ]] || continue
    base="$(basename "$norm" _norm.mp4)"
    device_dir="$(basename "$(dirname "$norm")")"
    proxy="$PROXY_DIR/$device_dir/${base}_proxy.mp4"
    raw_match=""

    # Find matching raw file
    for ext in MP4 MOV mp4 mov; do
      for candidate in "$RAW_DIR/$device_dir/$base.$ext"; do
        [[ -f "$candidate" ]] && raw_match="$candidate" && break 2
      done
    done

    # Get resolution/fps of normalized file
    res="$(ffprobe -v error -select_streams v:0 \
      -show_entries stream=width,height,r_frame_rate \
      -of csv=p=0 "$norm" 2>/dev/null || echo "?,?,?")"

    $first || echo "," >> "$MANIFEST"
    first=false
    cat >> "$MANIFEST" <<ENTRY
  {
    "device": "$device_dir",
    "original": "${raw_match:-unknown}",
    "normalized": "$norm",
    "proxy": "$([[ -f "$proxy" ]] && echo "$proxy" || echo "none")",
    "resolution_fps": "$res"
  }
ENTRY
  done
  echo "]" >> "$MANIFEST"
fi

log "================================================"
log "Done."
log "Normalized: $NORM_DIR"
log "Proxies:    $PROXY_DIR"
log "Manifest:   $MANIFEST"
log "================================================"
