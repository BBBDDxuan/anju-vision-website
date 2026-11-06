import analysis from '../../public/assets/analysis/wave/wave_analysis.json';
import type { DetectionSequence } from './types';

// Shared contract; this approved demo remains distinct from live inference.
export const waveDetectionDemo = analysis as DetectionSequence;
