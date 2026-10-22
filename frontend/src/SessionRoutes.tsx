import {
  Alert,
  Button,
  Container,
  IconButton,
  InputAdornment,
  Paper,
  Stack,
  SvgIcon,
  TextField,
  Typography,
} from '@mui/material';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Navigate, Route, Routes } from 'react-router';

import { ApiError, exchangeCode, getCurrentMember, logout } from './api';
import { Board } from './Board';
import { errorMessage } from './errors';
import type { Messages } from './locale';

function clearNonSessionQueries(
  client: ReturnType<typeof useQueryClient>,
): void {
  client.removeQueries({
    predicate: (query) => query.queryKey[0] !== 'session',
  });
}

function VisibilityIcon(props: React.ComponentProps<typeof SvgIcon>) {
  return (
    <SvgIcon {...props}>
      <path d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z" />
    </SvgIcon>
  );
}

function VisibilityOffIcon(props: React.ComponentProps<typeof SvgIcon>) {
  return (
    <SvgIcon {...props}>
      <path d="M12 7c2.76 0 5 2.24 5 5 0 .65-.13 1.26-.36 1.83l2.92 2.92c1.51-1.26 2.7-2.89 3.44-4.75-1.73-4.39-6-7.5-11-7.5-1.4 0-2.74.25-3.98.7l2.16 2.16C10.74 7.13 11.35 7 12 7zM2 4.27l2.28 2.28.46.46C3.08 8.3 1.78 10.02 1 12c1.73 4.39 6 7.5 11 7.5 1.55 0 3.03-.3 4.38-.84l.42.42L19.73 22 21 20.73 3.27 3 2 4.27zM7.53 9.8l1.55 1.55c-.05.21-.08.43-.08.65 0 1.66 1.34 3 3 3 .22 0 .44-.03.65-.08l1.55 1.55c-.67.33-1.41.53-2.2.53-2.76 0-5-2.24-5-5 0-.79.2-1.53.53-2.2zm4.31-.78l3.15 3.15.02-.16c0-1.66-1.34-3-3-3l-.17.01z" />
    </SvgIcon>
  );
}

function Login({ messages }: { messages: Messages }) {
  const client = useQueryClient();
  const [code, setCode] = useState('');
  const [invalid, setInvalid] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const login = useMutation({
    mutationFn: exchangeCode,
    onSuccess: async (member) => {
      await client.cancelQueries({ queryKey: ['session'] });
      client.setQueryData(['session'], member);
    },
  });

  return (
    <Container component="main" maxWidth="sm" sx={{ py: { xs: 4, md: 6 } }}>
      <Paper variant="outlined" sx={{ p: { xs: 3, sm: 5 } }}>
        <Typography variant="h2" sx={{ mb: 2 }}>
          {messages.loginTitle}
        </Typography>
        <Typography color="text.secondary" sx={{ mb: 3 }}>
          {messages.loginInstructions}
        </Typography>
        <Stack
          component="form"
          noValidate
          spacing={3}
          onSubmit={(event) => {
            event.preventDefault();
            if (login.isPending) return;
            if (!/^[A-Za-z0-9_-]{32}$/.test(code.trim())) {
              setInvalid(true);
              login.reset();
              return;
            }
            setInvalid(false);
            login.mutate(code.trim());
            setCode('');
          }}
        >
          <TextField
            fullWidth
            type={showPassword ? 'text' : 'password'}
            label={messages.loginCode}
            value={code}
            autoComplete="off"
            error={invalid}
            helperText={invalid ? messages.codeFormat : undefined}
            disabled={login.isPending}
            onPaste={(event) => {
              const pasted = event.clipboardData.getData('text');
              const match = pasted.match(/[A-Za-z0-9_-]{32}/);
              if (match) {
                event.preventDefault();
                setCode(match[0]);
                setInvalid(false);
                login.reset();
              }
            }}
            onChange={(event) => {
              const value = event.target.value;
              const match = value.match(/[A-Za-z0-9_-]{32}/);
              if (match && value.length > 32) {
                setCode(match[0]);
              } else {
                setCode(value);
              }
              setInvalid(false);
              login.reset();
            }}
            slotProps={{
              htmlInput: {
                autoCapitalize: 'none',
                autoCorrect: 'off',
                spellCheck: false,
              },
              input: {
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton
                      aria-label={
                        showPassword
                          ? messages.hidePassword
                          : messages.showPassword
                      }
                      onClick={() => setShowPassword((show) => !show)}
                      edge="end"
                      size="small"
                      disabled={login.isPending}
                    >
                      {showPassword ? (
                        <VisibilityOffIcon />
                      ) : (
                        <VisibilityIcon />
                      )}
                    </IconButton>
                  </InputAdornment>
                ),
              },
            }}
          />
          {login.isError && (
            <Alert severity="error">
              {errorMessage(login.error, messages)}
            </Alert>
          )}
          <Button type="submit" variant="contained" disabled={login.isPending}>
            {login.isPending ? messages.signingIn : messages.signIn}
          </Button>
        </Stack>
      </Paper>
    </Container>
  );
}

export function SessionRoutes({ messages }: { messages: Messages }) {
  const client = useQueryClient();
  const session = useQuery({
    queryKey: ['session'],
    queryFn: async () => {
      try {
        return await getCurrentMember();
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    retry: false,
    refetchInterval: 30_000,
    refetchOnWindowFocus: 'always',
    refetchOnReconnect: 'always',
  });
  useEffect(() => {
    if (session.data === null) {
      clearNonSessionQueries(client);
    }
  }, [client, session.data]);
  const signOut = useMutation({
    mutationFn: logout,
    onSuccess: async () => {
      await client.cancelQueries();
      clearNonSessionQueries(client);
      client.setQueryData(['session'], null);
    },
  });

  if (session.isPending) {
    return (
      <Container component="main" sx={{ py: 5 }}>
        <Typography role="status">{messages.sessionLoading}</Typography>
      </Container>
    );
  }
  if (session.isError) {
    return (
      <Container component="main" maxWidth="sm" sx={{ py: 5 }}>
        <Alert severity="error" sx={{ mb: 3 }}>
          {errorMessage(session.error, messages)}
        </Alert>
        <Button
          variant="contained"
          onClick={() => void session.refetch()}
          disabled={session.isFetching}
        >
          {messages.retry}
        </Button>
      </Container>
    );
  }

  return (
    <Routes>
      <Route
        path="/login"
        element={
          session.data ? (
            <Navigate to="/" replace />
          ) : (
            <Login messages={messages} />
          )
        }
      />
      <Route
        path="*"
        element={
          session.data ? (
            <>
              <Container maxWidth="lg" sx={{ pt: 3 }}>
                <Stack
                  direction="row"
                  spacing={2}
                  sx={{ alignItems: 'center', justifyContent: 'space-between' }}
                >
                  <Typography>{session.data.telegram_id}</Typography>
                  <Button
                    onClick={() => signOut.mutate()}
                    disabled={signOut.isPending}
                  >
                    {messages.signOut}
                  </Button>
                </Stack>
                {signOut.isError && (
                  <Alert severity="error">
                    {errorMessage(signOut.error, messages)}
                  </Alert>
                )}
              </Container>
              <Board messages={messages} />
            </>
          ) : (
            <Navigate to="/login" replace />
          )
        }
      />
    </Routes>
  );
}
