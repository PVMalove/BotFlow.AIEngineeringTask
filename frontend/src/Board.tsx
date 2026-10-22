import { Box, Chip, Container, Paper, Stack, Typography } from '@mui/material';

import type { Messages } from './locale';

export function Board({ messages }: { messages: Messages }) {
  return (
    <Container component="main" maxWidth="lg" sx={{ py: { xs: 4, md: 6 } }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        {messages.workspace}
      </Typography>
      <Typography variant="h2" sx={{ mb: 1.5 }}>
        {messages.board}
      </Typography>
      <Typography color="text.secondary" sx={{ mb: 4 }}>
        {messages.boardDescription}
      </Typography>
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: {
            xs: 'minmax(0, 1fr)',
            md: 'minmax(0, 1.6fr) minmax(0, 1fr)',
          },
          gap: 3,
        }}
      >
        <Paper
          variant="outlined"
          sx={{
            p: { xs: 3, sm: 5 },
            minWidth: 0,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-start',
            justifyContent: 'center',
          }}
        >
          <Chip
            label={messages.preparingLabel}
            size="small"
            sx={{
              bgcolor: '#F4E4BB',
              color: 'text.primary',
              fontWeight: 600,
              mb: 4,
              height: 'auto',
              '& .MuiChip-label': { whiteSpace: 'normal', py: 0.5 },
            }}
          />
          <Box
            aria-hidden="true"
            sx={{
              width: 56,
              height: 56,
              border: 1,
              borderColor: 'divider',
              borderRadius: '16px',
              display: 'grid',
              placeItems: 'center',
              mb: 3,
            }}
          >
            <Box sx={{ display: 'flex', gap: '4px', alignItems: 'flex-end' }}>
              {[24, 16, 30].map((height) => (
                <Box
                  key={height}
                  sx={{
                    width: 7,
                    height,
                    bgcolor: 'primary.main',
                    borderRadius: '3px',
                  }}
                />
              ))}
            </Box>
          </Box>
          <Typography variant="h3" sx={{ mb: 1.5 }}>
            {messages.preparing}
          </Typography>
          <Typography color="text.secondary">
            {messages.preparingDescription}
          </Typography>
        </Paper>
        <Paper
          component="aside"
          elevation={0}
          sx={{
            bgcolor: 'secondary.main',
            color: 'secondary.contrastText',
            p: { xs: 3, sm: 4 },
            minWidth: 0,
          }}
        >
          <Typography variant="h3" sx={{ mb: 3 }}>
            {messages.workflow}
          </Typography>
          <Stack
            component="ol"
            spacing={3}
            sx={{ m: 0, p: 0, listStyle: 'none' }}
          >
            {[
              [messages.telegramTitle, messages.telegramDescription],
              [messages.panelTitle, messages.panelDescription],
              [messages.teamTitle, messages.teamDescription],
            ].map(([title, description], index) => (
              <Stack component="li" direction="row" spacing={2} key={title}>
                <Box
                  component="span"
                  aria-hidden="true"
                  sx={{ color: 'primary.main', fontSize: 14, pt: 0.25 }}
                >
                  {String(index + 1).padStart(2, '0')}
                </Box>
                <Box sx={{ minWidth: 0 }}>
                  <Typography sx={{ fontWeight: 600, mb: 0.5 }}>
                    {title}
                  </Typography>
                  <Typography variant="body2" sx={{ color: '#D0CDC7' }}>
                    {description}
                  </Typography>
                </Box>
              </Stack>
            ))}
          </Stack>
        </Paper>
      </Box>
    </Container>
  );
}
