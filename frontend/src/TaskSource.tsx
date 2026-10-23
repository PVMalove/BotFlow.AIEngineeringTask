import { Box, SvgIcon } from '@mui/material';
import type { Task } from './api';
import type { Messages } from './locale';

const sourceIcons = {
  voice: {
    titleKey: 'taskSourceVoice',
    path: 'M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21H7v2h10v-2h-4v-3.08A7 7 0 0 0 19 11h-2Z',
  },
  text: {
    titleKey: 'taskSourceText',
    path: 'M20 2H4a2 2 0 0 0-2 2v18l4-4h14a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2Zm-1 11H5v-2h14v2Zm0-4H5V7h14v2Z',
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
          sx={{ fontSize: 18, color: 'text.primary', flexShrink: 0 }}
        >
          <path d={icon.path} />
        </SvgIcon>
      )}
      Telegram
    </Box>
  );
}
