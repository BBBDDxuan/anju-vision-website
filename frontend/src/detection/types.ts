/** Shared overlay contract. Demo fixtures and future per-run results use the
 * same coordinates/timestamps; the discriminated source must remain visible. */
export type DetectionSource =
  | { kind: 'demo'; basis: 'offline_model_with_reviewed_annotations' | 'model_review_with_reviewed_annotations'; notice: string;
      model: string; videoSha256: string; modelSha256: string;
      reviewExportSha256: string; reviewModel: string; reviewedAt: string;
      evidenceStartMs: number; evidenceEndMs: number;
      coordinateReview: 'visually_checked'; riskBasis: string }
  | { kind: 'demo'; basis: 'offline_pose_with_temporal_heuristic'; notice: string;
      model: string; modelSha256: string; videoSha256: string; poseExportSha256: string;
      method: string; ruleVersion: string; exportedAt: string; coordinateReview: 'visually_checked';
      trainedWaveClassifier: false; helpIntentEstablished: false }
  | { kind: 'inference'; runId: string; model: string; videoSha256: string; notice: string };

export type BoundingBox = { x: number; y: number; width: number; height: number };
export type Keypoint = { id: number; x: number; y: number; confidence: number };
export type DetectionObject = {
  id: string; className: string; trackId?: string; box: BoundingBox;
  polygon?: readonly { x: number; y: number }[]; confidence?: number;
  boxSource: 'reviewed_annotation' | 'offline_model' | 'inference';
};
export type DetectedPerson = {
  id: string; box: BoundingBox; confidence?: number; keypoints: readonly Keypoint[];
  boxSource: 'reviewed_annotation' | 'offline_model' | 'inference';
  keypointSource: 'offline_model' | 'inference';
  wave?: { eventId: string; startMs: number; endMs: number;
    wrists: readonly { side: 'left' | 'right'; keypointId: number;
      trajectory: readonly { timeMs: number; x: number; y: number }[] }[] };
};
export type DetectionFrame = {
  timeMs: number; frameIndex?: number; validUntilMs?: number;
  people: readonly DetectedPerson[]; objects?: readonly DetectionObject[];
  state: 'tracking' | 'suspected_fall' | 'suspected_fire' | 'waving'; labelVisible: boolean;
};
export type DetectionSequence = {
  schemaVersion: 1; source: DetectionSource;
  video: { width: number; height: number; durationMs: number };
  display: { startMs: number; endMs: number; fadeInMs: number; fadeOutMs: number };
  maxInterpolationGapMs: number; minimumKeypointConfidence: number;
  target?: 'person' | 'fire' | 'wave'; sampleMode?: 'interpolate' | 'frame';
  frames: readonly DetectionFrame[];
};

export const POSE_JOINTS = [0, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16] as const;
export const POSE_EDGES = [[0, 5], [0, 6], [5, 6], [5, 7], [7, 9], [6, 8], [8, 10],
  [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16]] as const;
