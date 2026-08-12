/**
 * Mirrors `app/services/render_contract.py::RenderProps.to_props()` field for
 * field. If you change one side, change the other -- there is no schema
 * validation between Python and this file, so a drift here fails silently
 * as a blank/wrong render rather than a type error.
 */

export type Branding = {
  agency_name: string;
  primary_color: string;
  secondary_color: string;
  logo_path: string | null;
  staff_name: string | null;
};

export type Clip = {
  source_path: string;
  duration_sec: number;
  // Set by the Python render layer from Photo.sky_replaced (spec §1.4).
  // Never omit rendering this when present -- it is a compliance requirement,
  // not a stylistic caption.
  disclosure_badge: string | null;
};

export type CaptionCue = {
  text: string;
  start_sec: number;
  end_sec: number;
};

export type RenderProps = {
  composition: string;
  width: number;
  height: number;
  fps: number;
  branding: Branding;
  clips: Clip[];
  captions: CaptionCue[];
  voiceover_path: string | null;
  music_path: string | null;
  music_duck_db: number;
  avatar_clip_path: string | null;
  lower_third: string | null;
};

export const defaultRenderProps: RenderProps = {
  composition: "Master16x9",
  width: 3840,
  height: 2160,
  fps: 30,
  branding: {
    agency_name: "",
    primary_color: "#111827",
    secondary_color: "#6b7280",
    logo_path: null,
    staff_name: null,
  },
  clips: [],
  captions: [],
  voiceover_path: null,
  music_path: null,
  music_duck_db: -14.0,
  avatar_clip_path: null,
  lower_third: null,
};

export type Segment = {
  text: string;
  visual_path: string;
  audio_path: string | null;
  duration_sec: number;
  captions: CaptionCue[];
  is_avatar: boolean;
  disclosure_badge: string | null;
};

export type SegmentedRenderProps = {
  composition: string;
  width: number;
  height: number;
  fps: number;
  branding: Branding;
  segments: Segment[];
  music_path: string | null;
  music_duck_db: number;
  lower_third: string | null;
};

export const defaultSegmentedRenderProps: SegmentedRenderProps = {
  composition: "Master16x9",
  width: 3840,
  height: 2160,
  fps: 30,
  branding: {
    agency_name: "",
    primary_color: "#111827",
    secondary_color: "#6b7280",
    logo_path: null,
    staff_name: null,
  },
  segments: [],
  music_path: null,
  music_duck_db: -14.0,
  lower_third: null,
};
