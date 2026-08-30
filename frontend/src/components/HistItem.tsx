import { useTranslation } from 'react-i18next';

import type { HistoryRow } from '../api/types';
import { GlassButton } from './GlassButton';

// One clickable history row (.hist-item). Clicking the row shows saved feedback;
// the two buttons view feedback / play the recording (mirrors histItemHtml in app.js).
interface HistItemProps {
  row: HistoryRow;
  onShowFeedback: () => void;
  onPlay: () => void;
  // Optional: when provided (local/appliance) a danger "delete" button is shown.
  onDelete?: () => void;
}

export function HistItem({ row, onShowFeedback, onPlay, onDelete }: HistItemProps) {
  const { t } = useTranslation();
  const date = (row.created_at || '').slice(0, 16).replace('T', ' ');
  const name = (row.video_path || '').split('/').pop() || t('feedback.noFile');
  const score = row.overall_score != null ? row.overall_score : t('feedback.noScore');

  return (
    <div className="hist-item" onClick={onShowFeedback}>
      <span className="hist-item-score">{score}</span>
      <div className="hist-item-info">
        <div className="hist-item-name">{name}</div>
        <div className="hist-item-date">{date}</div>
      </div>
      <div className="hist-item-actions">
        <GlassButton
          sm
          onClick={(e) => {
            e.stopPropagation();
            onShowFeedback();
          }}
        >
          {t('feedback.viewFeedback')}
        </GlassButton>
        <GlassButton
          sm
          onClick={(e) => {
            e.stopPropagation();
            onPlay();
          }}
        >
          {t('feedback.play')}
        </GlassButton>
        {onDelete && (
          <GlassButton
            sm
            variant="danger"
            aria-label={t('delete.videoAria')}
            title={t('delete.videoTitle')}
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
          >
            🗑
          </GlassButton>
        )}
      </div>
    </div>
  );
}
