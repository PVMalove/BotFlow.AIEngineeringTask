import {
  Box,
  Container,
  CssBaseline,
  Stack,
  ThemeProvider,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';

import { useLocale } from './locale';
import { SessionRoutes } from './SessionRoutes';
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
      <SessionRoutes messages={messages} />
    </ThemeProvider>
  );
}
