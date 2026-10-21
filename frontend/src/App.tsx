import {
  Box,
  Chip,
  Container,
  CssBaseline,
  Paper,
  Stack,
  ThemeProvider,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';

import { useLocale } from './locale';
import { theme } from './theme';

export default function App() {
  const { locale, selectLocale, messages } = useLocale();

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box
        component="header"
        sx={{
          bgcolor: 'background.paper',
          borderBottom: 1,
          borderColor: 'divider',
        }}
      >
        <Container maxWidth="lg" sx={{ py: 2.5 }}>
          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={2.5}
            sx={{
              justifyContent: 'space-between',
              alignItems: { xs: 'flex-start', sm: 'center' },
            }}
          >
            <Stack
              direction="row"
              spacing={1.5}
              sx={{ alignItems: 'center', minWidth: 0 }}
            >
              <Box
                aria-hidden="true"
                sx={{
                  width: 40,
                  height: 40,
                  flexShrink: 0,
                  display: 'grid',
                  placeItems: 'center',
                  bgcolor: 'primary.main',
                  color: 'primary.contrastText',
                  borderRadius: '12px',
                  fontSize: 18,
                  fontWeight: 600,
                }}
              >
                B.
              </Box>
              <Box sx={{ minWidth: 0 }}>
                <Typography variant="h1">BotFlow</Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ maxWidth: 380 }}
                >
                  {messages.tagline}
                </Typography>
              </Box>
            </Stack>
            <ToggleButtonGroup
              exclusive
              value={locale}
              aria-label={messages.language}
              onChange={(_, value: unknown) => {
                if (value === 'ru' || value === 'hy') selectLocale(value);
              }}
              sx={{ flexShrink: 0 }}
            >
              <ToggleButton value="ru" lang="ru">
                Русский
              </ToggleButton>
              <ToggleButton value="hy" lang="hy">
                Հայերեն
              </ToggleButton>
            </ToggleButtonGroup>
          </Stack>
        </Container>
      </Box>
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
    </ThemeProvider>
  );
}
