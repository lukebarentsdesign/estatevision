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
import type { RenderProps, Clip } from "./props";

/**
 * Renders one clip in the timeline, with the §1.4 disclosure badge composited
 * unconditionally whenever `disclosure_badge` is set. This component has no
 * prop to suppress it -- that mirrors `render_contract.build_render_props`,
 * which derives the badge and gives callers no way to turn it off.
 */
const ClipShot: React.FC<{ clip: Clip; frame: number; durationInFrames: number }> = ({
  clip,
  frame,
  durationInFrames,
}) => {
  // Quick cross-fade in/out so cuts aren't jarring.
  const fadeFrames = Math.min(10, Math.floor(durationInFrames / 4));
  const opacity = interpolate(
    frame,
    [0, fadeFrames, durationInFrames - fadeFrames, durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  return (
    <AbsoluteFill style={{ opacity }}>
      <Video src={clip.source_path} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      {clip.disclosure_badge && (
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
          {clip.disclosure_badge}
        </div>
      )}
    </AbsoluteFill>
  );
};

const LowerThird: React.FC<{ text: string; branding: RenderProps["branding"] }> = ({
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

const Captions: React.FC<{ cues: RenderProps["captions"]; fps: number }> = ({ cues, fps }) => {
  const frame = useCurrentFrame();
  const t = frame / fps;
  const active = cues.find((c) => t >= c.start_sec && t < c.end_sec);
  if (!active) return null;
  return (
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
      {active.text}
    </div>
  );
};

export const PropertyVideo: React.FC<RenderProps> = (props) => {
  const { fps } = useVideoConfig();
  let cursorFrame = 0;

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {props.clips.map((clip, i) => {
        const durationInFrames = Math.round(clip.duration_sec * fps);
        const from = cursorFrame;
        cursorFrame += durationInFrames;
        return (
          <Sequence key={i} from={from} durationInFrames={durationInFrames}>
            <ClipShot clip={clip} frame={0} durationInFrames={durationInFrames} />
          </Sequence>
        );
      })}

      {props.lower_third && <LowerThird text={props.lower_third} branding={props.branding} />}
      <Captions cues={props.captions} fps={fps} />

      {props.voiceover_path && <Audio src={props.voiceover_path} />}
      {props.music_path && (
        <Audio src={props.music_path} volume={Math.pow(10, props.music_duck_db / 20)} />
      )}
    </AbsoluteFill>
  );
};
