import { CssBaseline, Container, Typography } from '@mui/material';

export default function App() {
  return (
    <>
      <CssBaseline />
      <Container maxWidth="md" sx={{ py: 8 }}>
        <Typography variant="h2" component="h1">
          BotFlow
        </Typography>
        <Typography variant="h6" sx={{ mt: 2 }}>
          Задачи команды через Telegram и веб-панель
        </Typography>
        <Typography color="text.secondary" sx={{ mt: 2 }}>
          Панель готовится к запуску.
        </Typography>
      </Container>
    </>
  );
}
