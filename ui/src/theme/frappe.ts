/**
 * Thème PrimeVue d'inspiration « Frappe » : gris neutres, bouton principal sombre, champs en aplat gris clair sans bordure,
 * coins de 8 px, texte compact en Inter. Ce sont des jetons de conception écrits ici, pas des feuilles de style reprises
 * de Frappe : on reproduit l'ambiance, pas le code.
 *
 * Deux schémas : clair et sombre. Le sombre s'active par la classe `.app-dark` sur `<html>` (lib/theme.ts : choix de l'utilisateur ou
 * préférence du système). Les composants citent des jetons SÉMANTIQUES (`{content.background}`…), jamais un gris écrit en dur : c'est
 * ce qui les fait suivre le schéma.
 */
import Aura from '@primeuix/themes/aura'
import { definePreset } from '@primeuix/themes'

/** Échelle de gris du Desk, du plus clair au plus sombre. */
const gray = {
  0: '#ffffff',
  50: '#f8f8f8',
  100: '#f3f3f3',
  200: '#ededed',
  300: '#e2e2e2',
  400: '#c7c7c7',
  500: '#999999',
  600: '#7c7c7c',
  700: '#525252',
  800: '#383838',
  900: '#171717',
  950: '#0f0f0f',
}

export const FrappePreset = definePreset(Aura, {
  semantic: {
    transitionDuration: '0.15s',
    // Le bouton principal et les accents sont les gris les plus sombres, comme dans le Desk de Frappe.
    primary: { 50: gray[50], 100: gray[100], 200: gray[200], 300: gray[300], 400: gray[400], 500: gray[900], 600: gray[800], 700: gray[700], 800: gray[800], 900: gray[900], 950: gray[950] },
    focusRing: { width: '2px', style: 'solid', color: gray[300], offset: '1px' },
    formField: {
      paddingX: '0.625rem',
      paddingY: '0.25rem',
      borderRadius: '8px',
      focusRing: { width: '0', style: 'none', color: 'transparent', offset: '0', shadow: `0 0 0 2px ${gray[300]}` },
    },
    list: { option: { padding: '0.375rem 0.625rem', borderRadius: '{border.radius.md}' } },
    content: { borderRadius: '{border.radius.lg}' },
    navigation: { item: { padding: '0.375rem 0.625rem', borderRadius: '{border.radius.md}' } },
    colorScheme: {
      light: {
        surface: gray,
        primary: { color: gray[900], contrastColor: '#ffffff', hoverColor: gray[800], activeColor: gray[700] },
        highlight: { background: gray[100], focusBackground: gray[200], color: gray[900], focusColor: gray[900] },
        formField: {
          background: gray[100],
          disabledBackground: gray[100],
          filledBackground: gray[100],
          borderColor: gray[100],
          hoverBorderColor: gray[200],
          focusBorderColor: gray[400],
          invalidBorderColor: '{red.400}',
          color: gray[900],
          placeholderColor: gray[500],
          shadow: 'none',
        },
        text: { color: gray[900], hoverColor: gray[900], mutedColor: gray[600], hoverMutedColor: gray[700] },
        content: { background: gray[0], hoverBackground: gray[50], borderColor: gray[200] },
        overlay: {
          select: { borderColor: gray[200] },
          popover: { borderColor: gray[200] },
          modal: { borderColor: gray[200] },
        },
        mask: { background: 'rgba(23, 23, 23, 0.35)' },
      },
      dark: {
        surface: gray,
        primary: { color: gray[50], contrastColor: gray[900], hoverColor: gray[200], activeColor: gray[300] },
        highlight: { background: gray[800], focusBackground: gray[700], color: gray[50], focusColor: gray[50] },
        formField: {
          background: gray[800],
          disabledBackground: gray[800],
          filledBackground: gray[800],
          borderColor: gray[800],
          hoverBorderColor: gray[700],
          focusBorderColor: gray[600],
          invalidBorderColor: '{red.400}',
          color: gray[50],
          placeholderColor: gray[500],
          shadow: 'none',
        },
        text: { color: gray[50], hoverColor: gray[0], mutedColor: gray[400], hoverMutedColor: gray[300] },
        content: { background: gray[900], hoverBackground: gray[800], borderColor: gray[800] },
        overlay: {
          select: { borderColor: gray[700], background: gray[900] },
          popover: { borderColor: gray[700], background: gray[900] },
          modal: { borderColor: gray[700], background: gray[900] },
        },
        mask: { background: 'rgba(0, 0, 0, 0.6)' },
      },
    },
  },
  components: {
    button: {
      root: { label: { fontWeight: '500' }, paddingX: '0.75rem', paddingY: '0.25rem', borderRadius: '8px' },
      colorScheme: {
        light: {
          root: {
            secondary: { background: gray[100], hoverBackground: gray[200], activeBackground: gray[300], borderColor: gray[100], hoverBorderColor: gray[200], activeBorderColor: gray[300], color: gray[900], hoverColor: gray[900], activeColor: gray[900] },
          },
        },
        dark: {
          root: {
            secondary: { background: gray[800], hoverBackground: gray[700], activeBackground: gray[600], borderColor: gray[800], hoverBorderColor: gray[700], activeBorderColor: gray[600], color: gray[50], hoverColor: gray[50], activeColor: gray[50] },
          },
        },
      },
    },
    datatable: {
      headerCell: { background: '{content.background}', color: '{text.muted.color}', padding: '6px 8px', borderColor: '{content.border.color}' },
      columnTitle: { fontWeight: '500' },
      bodyCell: { padding: '4px 8px', borderColor: '{content.border.color}' },
      row: { hoverBackground: '{content.hover.background}', selectedBackground: '{highlight.background}', selectedColor: '{highlight.color}' },
    },
    paginator: { root: { padding: '0.5rem 0.75rem' }, navButton: { width: '2rem', height: '2rem', borderRadius: '{border.radius.md}', selectedBackground: '{highlight.background}' } },
    dialog: { root: { borderRadius: '{border.radius.xl}' }, title: { fontSize: '1.125rem', fontWeight: '600' } },
    toast: { root: { width: '22rem' } },
    tag: { root: { fontSize: '0.75rem', fontWeight: '500', padding: '0.125rem 0.5rem' } },
  },
})

export const primeVueOptions = {
  theme: { preset: FrappePreset, options: { darkModeSelector: '.app-dark', cssLayer: false } },
}
