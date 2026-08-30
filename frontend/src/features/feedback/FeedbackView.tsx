import { forwardRef } from 'react';
import { useTranslation } from 'react-i18next';

import type { Feedback, Metrics } from '../../api/types';
import { DotList } from '../../components/DotList';
import { ScoreBadge } from '../../components/ScoreBadge';
import { WidgetCard } from '../../components/WidgetCard';

// Data the feedback card renders: local metrics + optional Claude feedback (+ error).
// Shared by the live analyze result and by "view feedback" from history.
export interface FeedbackData {
  metrics: Metrics;
  feedback: Feedback | null;
  feedback_error: string | null;
}

// Full feedback render, matching renderFeedback() in app.js 1:1.
export const FeedbackView = forwardRef<HTMLDivElement, { data: FeedbackData }>(function FeedbackView(
  { data },
  ref,
) {
  const { t } = useTranslation();
  const m = data.metrics;
  const fb = data.feedback;

  const metricRows: Array<[string, string | number]> = [
    [t('feedback.metric.language'), m.language],
    [t('feedback.metric.duration'), t('feedback.unitSeconds', { v: Math.round(m.duration_s) })],
    [t('feedback.metric.pace'), t('feedback.unitWpm', { v: Math.round(m.wpm) })],
    [
      t('feedback.metric.fillers'),
      t('feedback.fillersValue', {
        count: m.filler_count,
        perMin: m.fillers_per_min.toFixed(1),
      }),
    ],
    [t('feedback.metric.longPauses'), m.long_pause_count],
    [t('feedback.metric.silence'), Math.round(m.pause_ratio * 100) + '%'],
  ];

  // Nonverbal metrics (local MediaPipe) — only present when the vision stage ran.
  const nv = m.nonverbal;
  const pct = (v: number) => Math.round(v * 100) + '%';
  const nonverbalRows: Array<[string, string | number]> = nv
    ? [
        [t('nonverbal.metric.eyeContact'), pct(nv.eye_contact_ratio)],
        [t('nonverbal.metric.smile'), pct(nv.smile_ratio)],
        [t('nonverbal.metric.posture'), pct(nv.posture_upright_ratio)],
        [t('nonverbal.metric.gestures'), t('nonverbal.perMin', { v: nv.gesture_rate_per_min.toFixed(1) })],
        [t('nonverbal.metric.stability'), pct(nv.head_stability)],
        [t('nonverbal.metric.blink'), t('nonverbal.perMin', { v: nv.blink_rate_per_min.toFixed(1) })],
      ]
    : [];

  // Summary + score depend on whether Claude feedback is present.
  const score = fb ? fb.overall_score : t('feedback.noScore');
  const summary = fb
    ? fb.summary
    : data.feedback_error
      ? t('feedback.feedbackOmitted', { err: data.feedback_error })
      : t('feedback.noFeedbackConfigured');

  const improvements = (fb?.improvements ?? []).map(
    (i) => `[${i.area}] ${i.issue} → ${i.suggestion}`,
  );
  const rewrites = fb?.rewrites ?? [];

  let costText = '';
  if (fb) {
    const total = (fb.input_tokens || 0) + (fb.output_tokens || 0);
    if (total) {
      const cost = fb.cost_usd
        ? t('feedback.costUsd', { v: fb.cost_usd.toFixed(4) })
        : t('feedback.priceNotSet');
      costText = t('feedback.cost', {
        input: fb.input_tokens,
        output: fb.output_tokens,
        total,
        cost,
        model: fb.model,
      });
    }
  }

  return (
    <WidgetCard wide>
      <div ref={ref} style={{ display: 'flex', alignItems: 'baseline', gap: 16 }}>
        <ScoreBadge value={score} suffix={t('feedback.outOf')} />
        <div className="ai-insight" style={{ flex: 1 }}>
          {summary}
        </div>
      </div>

      <div style={{ marginTop: 24 }}>
        <div className="card-label">{t('feedback.metricsCard')}</div>
        <div className="metric-grid">
          {metricRows.map(([label, value], i) => (
            <div key={i}>
              <div className="metric-value">{value}</div>
              <div className="metric-label">{label}</div>
            </div>
          ))}
        </div>
      </div>

      {nv && (
        <div style={{ marginTop: 24 }}>
          <div className="card-label">{t('nonverbal.card')}</div>
          <div className="metric-grid">
            {nonverbalRows.map(([label, value], i) => (
              <div key={i}>
                <div className="metric-value">{value}</div>
                <div className="metric-label">{label}</div>
              </div>
            ))}
          </div>
          <div className="metric-label" style={{ marginTop: 8, opacity: 0.7 }}>
            {t('nonverbal.disclaimer')}
          </div>
        </div>
      )}

      <div className="grid" style={{ marginTop: 24 }}>
        <div>
          <div className="card-label">{t('feedback.strengths')}</div>
          <DotList items={fb?.strengths ?? []} dot="ok" />
        </div>
        <div>
          <div className="card-label">{t('feedback.toImprove')}</div>
          <DotList items={improvements} dot="warn" />
        </div>
      </div>

      {rewrites.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <div className="card-label">{t('feedback.rewrites')}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {rewrites.map((rw, i) => (
              <div className="rewrite" key={i}>
                <div className="before">{rw.original}</div>
                <div className="after">{rw.improved}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ marginTop: 24 }}>
        <div className="card-label">{t('feedback.goals')}</div>
        <DotList items={fb?.next_session_goals ?? []} dot="accent" />
      </div>

      {costText && <div className="cost-note">{costText}</div>}
    </WidgetCard>
  );
});
