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
        <Container maxWidth="lg" sx={{ py: 2 }}>
          <Stack
            direction="row"
            spacing={1.5}
            sx={{
              justifyContent: 'space-between',
              alignItems: 'center',
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
              </Box>
            </Stack>
            <ToggleButtonGroup
              exclusive
              value={locale}
              aria-label={messages.language}
              onChange={(_, value: unknown) => {
                if (value === 'ru' || value === 'hy') selectLocale(value);
              }}
              sx={{
                flexShrink: 0,
                p: '3px',
                gap: '3px',
                bgcolor: 'background.default',
                border: 1,
                borderColor: 'divider',
                borderRadius: '12px',
                '& .MuiToggleButtonGroup-grouped': {
                  m: 0,
                  border: 0,
                  borderRadius: '8px',
                  minWidth: 48,
                  minHeight: 36,
                  px: 1.5,
                  py: 0.75,
                  fontSize: 12,
                  lineHeight: 1.5,
                  color: 'text.secondary',
                  '&:hover': { bgcolor: 'action.hover' },
                  '&.Mui-selected': {
                    bgcolor: 'background.paper',
                    color: 'text.primary',
                    boxShadow: '0 1px 4px rgba(31, 30, 28, 0.12)',
                    '&:hover': { bgcolor: 'background.paper' },
                  },
                },
              }}
            >
              <ToggleButton
                value="ru"
                lang="ru"
                aria-label="Русский"
                title="Русский"
              >
                RU
              </ToggleButton>
              <ToggleButton
                value="hy"
                lang="hy"
                aria-label="Հայերեն"
                title="Հայերեն"
              >
                ՀԱՅ
              </ToggleButton>
            </ToggleButtonGroup>
          </Stack>
        </Container>
      </Box>
      <SessionRoutes messages={messages} locale={locale} />
    </ThemeProvider>
  );
}
