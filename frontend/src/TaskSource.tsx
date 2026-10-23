import { Box, SvgIcon } from '@mui/material';
import type { Task } from './api';
import type { Messages } from './locale';

// Keep the reference silhouettes legible at 12px: grille, stand and chat dots.
const sourceIcons = {
  voice: {
    titleKey: 'taskSourceVoice',
    shape: (
      <>
        <rect x="8" y="2" width="8" height="14" rx="4" />
        <path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M9 22h6" />
        <path d="M10.5 5h3M10.5 8h3M10.5 11h3" strokeWidth="1.4" />
      </>
    ),
  },
  text: {
    titleKey: 'taskSourceText',
    shape: (
      <>
        <path d="M7 4h10a5 5 0 0 1 5 5v4a5 5 0 0 1-5 5h-6l-5 4v-4a5 5 0 0 1-4-5V9a5 5 0 0 1 5-5Z" />
        <g fill="currentColor" stroke="none">
          <circle cx="7" cy="11" r="1.1" />
          <circle cx="12" cy="11" r="1.1" />
          <circle cx="17" cy="11" r="1.1" />
        </g>
      </>
    ),
  },
} as const;

export function TaskSource({
  source,
  messages,
}: {
  source: Task['source_type'];
  messages: Messages;
}) {
  const icon = source ? sourceIcons[source] : null;
  return (
    <Box
      component="span"
      sx={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 0.5,
        verticalAlign: 'middle',
      }}
    >
      {icon && (
        <SvgIcon
          titleAccess={messages[icon.titleKey]}
          sx={{
            fontSize: 12,
            width: 12,
            height: 12,
            color: 'text.primary',
            flexShrink: 0,
            fill: 'none',
            stroke: 'currentColor',
            strokeWidth: 1.8,
            strokeLinecap: 'round',
            strokeLinejoin: 'round',
          }}
        >
          {icon.shape}
        </SvgIcon>
      )}
      {messages.sourceTelegram}
    </Box>
  );
}
