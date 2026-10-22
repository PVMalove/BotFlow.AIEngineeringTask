import {
  Alert,
  Avatar,
  Box,
  Button,
  Chip,
  Container,
  Paper,
  Stack,
  Tab,
  Tabs,
  Typography,
} from '@mui/material';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { accessPageSize, ApiError, decideAccess, listAccess } from './api';
import type { AccessAction, AccessList, AdminMember, Member } from './api';
import type { Locale, Messages } from './locale';

function decisionError(error: unknown, messages: Messages): string {
  if (!(error instanceof ApiError)) return messages.decisionUnknown;
  if (error.code === 'cannot_block_self') return messages.cannotBlockSelf;
  if (error.code === 'last_admin') return messages.lastAdmin;
  if (error.status === 409 || error.status === 404)
    return messages.decisionConflict;
  if (error.status === 401 || error.status === 403) return messages.adminDenied;
  if (error.status === 429) return messages.rateLimited;
  return messages.decisionUnknown;
}

export function AdminAccess({
  member,
  messages,
  locale,
}: {
  member: Member;
  messages: Messages;
  locale: Locale;
}) {
  const client = useQueryClient();
  const [list, setList] = useState<AccessList>('pending');
  const [page, setPage] = useState(0);
  const key = ['admin', member.id];
  const members = useQuery({
    queryKey: [...key, list, page],
    queryFn: () => listAccess(list, page),
    retry: false,
  });
  // Reuse the first page queries for each filter. The API has no total metadata yet.
  const pendingCount = useQuery({
    queryKey: [...key, 'pending', 0],
    queryFn: () => listAccess('pending', 0),
    retry: false,
  });
  const rejectedCount = useQuery({
    queryKey: [...key, 'rejected', 0],
    queryFn: () => listAccess('rejected', 0),
    retry: false,
  });
  const memberCount = useQuery({
    queryKey: [...key, 'members', 0],
    queryFn: () => listAccess('members', 0),
    retry: false,
  });
  const count = (query: typeof pendingCount) =>
    query.isError
      ? '?'
      : query.data
        ? String(query.data.length) +
          (query.data.length === accessPageSize ? '+' : '')
        : '…';
  const decision = useMutation({
    mutationFn: ({
      target,
      action,
    }: {
      target: AdminMember;
      action: AccessAction;
    }) => decideAccess(target, action),
    retry: false,
    onSettled: async (_data, error) => {
      await client.cancelQueries({ queryKey: key });
      await client.invalidateQueries({ queryKey: key });
      if (error instanceof ApiError && [401, 403].includes(error.status)) {
        await client.invalidateQueries({ queryKey: ['session'] });
      }
    },
  });
  useEffect(() => {
    if (
      members.error instanceof ApiError &&
      [401, 403].includes(members.error.status)
    ) {
      void client.invalidateQueries({ queryKey: ['session'] });
    }
  }, [client, members.error]);

  const actions: Record<AdminMember['status'], AccessAction[]> = {
    pending: ['approve', 'reject', 'block'],
    rejected: ['block'],
    approved: ['block'],
    blocked: ['unblock'],
  };
  const statuses = {
    pending: messages.accessPending,
    rejected: messages.accessRejected,
    approved: messages.accessApproved,
    blocked: messages.accessBlocked,
  };
  const labels = {
    approve: messages.approve,
    reject: messages.reject,
    block: messages.block,
    unblock: messages.unblock,
  };
  const busy = decision.isPending || members.isFetching;
  const dateFormat = new Intl.DateTimeFormat(
    locale,
    Intl.DateTimeFormat.supportedLocalesOf([locale]).length
      ? { dateStyle: 'medium', timeStyle: 'short' }
      : {
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          hourCycle: 'h23',
        },
  );

  return (
    <Container component="main" maxWidth="lg" sx={{ py: { xs: 4, md: 6 } }}>
      <Typography variant="h2" sx={{ mb: 2 }}>
        {messages.adminTitle}
      </Typography>
      <Typography color="text.secondary" sx={{ mb: 3 }}>
        {messages.adminDescription}
      </Typography>
      <Tabs
        value={list}
        textColor="secondary"
        variant="scrollable"
        scrollButtons="auto"
        aria-label={messages.adminTitle}
        title={messages.loadedCount}
        onChange={(_, value: AccessList) => {
          setList(value);
          setPage(0);
          decision.reset();
        }}
        sx={{ mb: 3 }}
      >
        <Tab
          value="pending"
          label={messages.joinRequests + ' (' + count(pendingCount) + ')'}
          disabled={decision.isPending}
        />
        <Tab
          value="rejected"
          label={messages.rejectedRequests + ' (' + count(rejectedCount) + ')'}
          disabled={decision.isPending}
        />
        <Tab
          value="members"
          label={messages.members + ' (' + count(memberCount) + ')'}
          disabled={decision.isPending}
        />
      </Tabs>
      <Stack spacing={2}>
        {decision.isError && (
          <Alert severity="error">
            {decisionError(decision.error, messages)}
          </Alert>
        )}
        {decision.isSuccess && (
          <Alert severity={decision.data.changed ? 'success' : 'info'}>
            {decision.data.changed
              ? messages.decisionSaved
              : messages.decisionUnchanged}
          </Alert>
        )}
        {members.isFetching && (
          <Typography role="status">{messages.accessLoading}</Typography>
        )}
        {members.isError ? (
          <Alert severity="error">
            {members.error instanceof ApiError &&
            [401, 403].includes(members.error.status)
              ? messages.adminDenied
              : messages.accessLoadFailed}
          </Alert>
        ) : (
          <Box
            component="ul"
            aria-label={
              list === 'members'
                ? messages.members
                : list === 'pending'
                  ? messages.joinRequests
                  : messages.rejectedRequests
            }
            sx={{ listStyle: 'none', p: 0, m: 0, display: 'grid', gap: 2 }}
          >
            {members.data?.map((target) => (
              <Paper
                component="li"
                variant="outlined"
                key={target.id}
                sx={{ py: 2, px: { xs: 2, sm: 3 }, minWidth: 0 }}
              >
                <Stack
                  direction={{ xs: 'column', md: 'row' }}
                  useFlexGap
                  sx={{ gap: 1.5, alignItems: { md: 'center' } }}
                >
                  <Stack
                    direction="row"
                    spacing={2}
                    sx={{ alignItems: 'center', minWidth: 0, flex: 1 }}
                  >
                    <Avatar
                      aria-hidden="true"
                      sx={{
                        width: 44,
                        height: 44,
                        bgcolor: '#F4E8CD',
                        color: 'text.primary',
                        fontSize: 16,
                      }}
                    >
                      {(target.display_name ?? '?').slice(0, 1)}
                    </Avatar>
                    <Box sx={{ overflowWrap: 'anywhere' }}>
                      <Typography sx={{ fontWeight: 600, fontSize: 15 }}>
                        {target.display_name ?? messages.unnamedMember}
                      </Typography>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ fontSize: 12 }}
                      >
                        Telegram ID: {target.telegram_id}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {messages.requestedAt}:{' '}
                        {dateFormat.format(new Date(target.created_at))}
                      </Typography>
                    </Box>
                  </Stack>
                  <Stack
                    direction="row"
                    useFlexGap
                    sx={{ alignItems: 'center', flexWrap: 'wrap', gap: 1.5 }}
                  >
                    <Typography variant="body2" color="text.secondary">
                      {target.role === 'admin'
                        ? messages.roleAdmin
                        : messages.roleUser}
                    </Typography>
                    <Chip
                      label={statuses[target.status]}
                      sx={{
                        bgcolor:
                          target.status === 'approved' ? '#E8F2E9' : undefined,
                        color:
                          target.status === 'approved' ? '#2D6035' : undefined,
                        height: 'auto',
                        '& .MuiChip-label': { whiteSpace: 'normal', py: 0.5 },
                      }}
                    />
                    <Stack
                      direction="row"
                      useFlexGap
                      sx={{ flexWrap: 'wrap', gap: 1.5 }}
                    >
                      {actions[target.status].map((action) => (
                        <Button
                          key={action}
                          color={action === 'approve' ? 'primary' : 'secondary'}
                          variant={
                            action === 'approve' ? 'contained' : 'outlined'
                          }
                          disabled={busy || target.id === member.id}
                          onClick={() => decision.mutate({ target, action })}
                        >
                          {labels[action]}
                        </Button>
                      ))}
                    </Stack>
                  </Stack>
                </Stack>
              </Paper>
            ))}
          </Box>
        )}
        {!members.isPending &&
          !members.isError &&
          members.data?.length === 0 && (
            <Typography>
              {list === 'members' ? messages.noMembers : messages.noRequests}
            </Typography>
          )}
        {decision.isPending && (
          <Typography role="status">{messages.savingDecision}</Typography>
        )}
        <Stack
          direction="row"
          useFlexGap
          sx={{ flexWrap: 'wrap', gap: 1, alignItems: 'center' }}
        >
          <Button
            color="secondary"
            disabled={busy}
            onClick={() => {
              decision.reset();
              void members.refetch();
            }}
          >
            {messages.refreshAccess}
          </Button>
          <Button
            color="secondary"
            disabled={busy || page === 0}
            onClick={() => {
              setPage(page - 1);
              decision.reset();
            }}
          >
            {messages.previousPage}
          </Button>
          <Typography
            component="span"
            aria-current="page"
            aria-label={`${messages.page} ${page + 1}`}
            sx={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              minWidth: 36,
              height: 36,
              px: 1.25,
              borderRadius: '10px',
              backgroundColor: 'primary.main',
              color: 'primary.contrastText',
              fontWeight: 700,
              fontSize: '0.95rem',
              lineHeight: 1,
            }}
          >
            {new Intl.NumberFormat(locale).format(page + 1)}
          </Typography>
          <Button
            color="secondary"
            disabled={
              busy || members.isError || members.data?.length !== accessPageSize
            }
            onClick={() => {
              setPage(page + 1);
              decision.reset();
            }}
          >
            {messages.nextPage}
          </Button>
        </Stack>
      </Stack>
    </Container>
  );
}
