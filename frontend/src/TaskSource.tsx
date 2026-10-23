import { Box, SvgIcon } from '@mui/material';
import type { Task } from './api';
import type { Messages } from './locale';

// Outlined Telegram-style glyphs: a microphone and a chat bubble with a tail.
const sourceIcons = {
  voice: {
    titleKey: 'taskSourceVoice',
    shape: (
      <>
        <rect x="9" y="3" width="6" height="11" rx="3" />
        <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" />
      </>
    ),
  },
  text: {
    titleKey: 'taskSourceText',
    shape: (
      <path d="M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-7.5L6 21v-4H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z" />
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
            fontSize: 14,
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
      Telegram
    </Box>
  );
}
