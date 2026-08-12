import React from "react";
import {
  AbsoluteFill,
  Audio,
  Sequence,
  Video,
  useVideoConfig,
  interpolate,
  useCurrentFrame,
} from "remotion";
import type { SegmentedRenderProps, Segment } from "./props";

/**
 * Renders one self-contained segment: its own visual, its own audio, its own
 * captions, for exactly its own duration. Unlike the legacy ClipShot (which
 * relies on a duration handed down independently of any audio), a Segment's
 * duration always matches its own audio_path's actual length, produced
 * upstream by `services.script_audio.synthesize_and_slice_segments` --
 * this component has no way to desync visual and audio because they always
 * travel together as one unit.
 */
const SegmentShot: React.FC<{ segment: Segment; durationInFrames: number; fps: number }> = ({
  segment,
  durationInFrames,
  fps,
}) => {
  const frame = useCurrentFrame();
  const fadeFrames = Math.min(10, Math.floor(durationInFrames / 4));
  const opacity = interpolate(
    frame,
    [0, fadeFrames, durationInFrames - fadeFrames, durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const t = frame / fps;
  const activeCaption = segment.captions.find((c) => t >= c.start_sec && t < c.end_sec);

  return (
    <AbsoluteFill style={{ opacity }}>
      <Video src={segment.visual_path} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      {segment.disclosure_badge && (
        <div
          style={{
            position: "absolute",
            bottom: 24,
            right: 24,
            background: "rgba(0,0,0,0.72)",
            color: "#fff",
            padding: "8px 14px",
            borderRadius: 6,
            fontSize: 20,
            fontFamily: "sans-serif",
            fontWeight: 600,
          }}
        >
          {segment.disclosure_badge}
        </div>
      )}
      {activeCaption && (
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: 120,
            textAlign: "center",
            fontFamily: "sans-serif",
            fontSize: 40,
            fontWeight: 700,
            color: "#fff",
            textShadow: "0 2px 8px rgba(0,0,0,0.8)",
          }}
        >
          {activeCaption.text}
        </div>
      )}
      {/* is_avatar segments carry their own audio track inside visual_path's
          video file; only non-avatar segments need a separate <Audio> tag. */}
      {!segment.is_avatar && segment.audio_path && <Audio src={segment.audio_path} />}
    </AbsoluteFill>
  );
};

const LowerThird: React.FC<{ text: string; branding: SegmentedRenderProps["branding"] }> = ({
  text,
  branding,
}) => (
  <div
    style={{
      position: "absolute",
      left: 32,
      bottom: 32,
      padding: "10px 20px",
      background: branding.primary_color,
      color: "#fff",
      fontFamily: "sans-serif",
      fontSize: 28,
      fontWeight: 700,
      borderRadius: 4,
    }}
  >
    {text}
  </div>
);

export const SegmentedPropertyVideo: React.FC<SegmentedRenderProps> = (props) => {
  const { fps } = useVideoConfig();
  let cursorFrame = 0;

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {props.segments.map((segment, i) => {
        const durationInFrames = Math.round(segment.duration_sec * fps);
        const from = cursorFrame;
        cursorFrame += durationInFrames;
        return (
          <Sequence key={i} from={from} durationInFrames={durationInFrames}>
            <SegmentShot segment={segment} durationInFrames={durationInFrames} fps={fps} />
          </Sequence>
        );
      })}

      {props.lower_third && <LowerThird text={props.lower_third} branding={props.branding} />}

      {props.music_path && (
        <Audio src={props.music_path} volume={Math.pow(10, props.music_duck_db / 20)} />
      )}
    </AbsoluteFill>
  );
};
