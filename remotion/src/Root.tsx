import React from "react";
import { Composition, CalculateMetadataFunction } from "remotion";
import { PropertyVideo } from "./PropertyVideo";
import { SegmentedPropertyVideo } from "./SegmentedPropertyVideo";
import {
  RenderProps,
  defaultRenderProps,
  SegmentedRenderProps,
  defaultSegmentedRenderProps,
} from "./props";

/**
 * Width/height/duration all vary per job (master vs reel, clip count and
 * per-clip duration set by the pipeline), so they're derived from the actual
 * incoming props via calculateMetadata rather than fixed on <Composition>.
 * The fixed values on the tag below are only Remotion Studio preview
 * defaults; every real render overrides them via --props.
 */
const calculateMetadata: CalculateMetadataFunction<RenderProps> = ({ props }) => {
  const totalSeconds = props.clips.reduce((sum, c) => sum + c.duration_sec, 0) || 1;
  return {
    durationInFrames: Math.max(1, Math.round(totalSeconds * props.fps)),
    fps: props.fps,
    width: props.width,
    height: props.height,
    props,
  };
};

const calculateSegmentedMetadata: CalculateMetadataFunction<SegmentedRenderProps> = ({ props }) => {
  const totalSeconds = props.segments.reduce((sum, s) => sum + s.duration_sec, 0) || 1;
  return {
    durationInFrames: Math.max(1, Math.round(totalSeconds * props.fps)),
    fps: props.fps,
    width: props.width,
    height: props.height,
    props,
  };
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="Master16x9"
        component={PropertyVideo}
        durationInFrames={150}
        fps={30}
        width={3840}
        height={2160}
        defaultProps={defaultRenderProps}
        calculateMetadata={calculateMetadata}
      />
      <Composition
        id="Reel9x16"
        component={PropertyVideo}
        durationInFrames={150}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{ ...defaultRenderProps, composition: "Reel9x16", width: 1080, height: 1920 }}
        calculateMetadata={calculateMetadata}
      />
      <Composition
        id="SegmentedMaster16x9"
        component={SegmentedPropertyVideo}
        durationInFrames={150}
        fps={30}
        width={3840}
        height={2160}
        defaultProps={defaultSegmentedRenderProps}
        calculateMetadata={calculateSegmentedMetadata}
      />
      <Composition
        id="SegmentedReel9x16"
        component={SegmentedPropertyVideo}
        durationInFrames={150}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          ...defaultSegmentedRenderProps,
          composition: "Reel9x16",
          width: 1080,
          height: 1920,
        }}
        calculateMetadata={calculateSegmentedMetadata}
      />
    </>
  );
};
