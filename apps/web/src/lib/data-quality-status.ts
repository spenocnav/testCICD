import type { DataQualityMetric } from './types';

export type QualityTone = 'success' | 'warning' | 'critical';

export function healthScoreTone(score: number): QualityTone {
  if (score >= 90) return 'success';
  if (score >= 70) return 'warning';
  return 'critical';
}

export function metricTone(metric: DataQualityMetric): QualityTone {
  return metric.count === 0 ? 'success' : metric.severity;
}
