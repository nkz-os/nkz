import { beforeMount } from '@playwright/experimental-ct-react/hooks';
import './index.css';
import '../../design-tokens/dist/tokens.css';

beforeMount<{ profile?: string }>(async ({ hooksConfig }) => {
  const profile = hooksConfig?.profile ?? 'page';
  document.documentElement.setAttribute('data-theme', profile);
  document.body.style.margin = '0';
  document.body.style.padding = '16px';
  // tokens.css trae Inter de Google Fonts con display=swap: el primer pintado usa
  // la pila de respaldo y cambia cuando llega la red. Sin esta espera, cada captura
  // compite con ese cambio. `fonts.ready` resuelve también si la carga falla, así
  // que no puede colgarse sin conexión.
  await document.fonts.ready;
});
