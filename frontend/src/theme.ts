import { createTheme } from '@mui/material/styles';

export const theme = createTheme({
  palette: {
    primary: { main: '#DCAF46', dark: '#B78A25', contrastText: '#282525' },
    secondary: { main: '#282525', contrastText: '#FFFFFF' },
    background: { default: '#F6F5F2', paper: '#FFFFFF' },
    text: { primary: '#282525', secondary: '#64615B' },
    divider: '#E6E3DC',
  },
  typography: {
    fontFamily: 'Montserrat, "Noto Sans Armenian", sans-serif',
    fontWeightMedium: 600,
    fontWeightBold: 600,
    h1: { fontSize: '1.5rem', fontWeight: 600, letterSpacing: '-0.04em' },
    h2: {
      fontSize: 'clamp(1.6rem, 4vw, 2.25rem)',
      fontWeight: 600,
      lineHeight: 1.3,
    },
    h3: { fontSize: '1.2rem', fontWeight: 600, lineHeight: 1.5 },
    body1: { lineHeight: 1.8 },
    body2: { lineHeight: 1.7 },
    button: { textTransform: 'none', fontWeight: 600 },
  },
  shape: { borderRadius: 16 },
  components: {
    MuiButtonBase: {
      styleOverrides: {
        root: {
          '&:focus-visible, &.Mui-focusVisible': {
            outline: '3px solid #282525',
            outlineOffset: '4px',
          },
        },
      },
    },
    MuiCssBaseline: {
      styleOverrides: {
        body: { minWidth: 0 },
        'html[lang="hy"]': {
          fontSize: '14.5px',
        },
        ':lang(hy)': {
          fontFamily: '"Noto Sans Armenian", Montserrat, sans-serif',
          letterSpacing: '-0.01em',
        },
        'h1, h2, h3, p': { overflowWrap: 'anywhere' },
        ':focus-visible': {
          outline: '3px solid #282525',
          outlineOffset: '4px',
        },
        '.Mui-focusVisible': {
          outline: '3px solid #282525',
          outlineOffset: '4px',
        },
        '::selection': { backgroundColor: '#F4E4BB', color: '#282525' },
      },
    },
    MuiToggleButton: {
      styleOverrides: {
        root: {
          color: '#64615B',
          padding: '8px 14px',
          minHeight: 44,
          '&.Mui-selected': {
            backgroundColor: '#DCAF46',
            color: '#282525',
            '&:hover': { backgroundColor: '#E6BF61' },
          },
        },
      },
    },
  },
});
