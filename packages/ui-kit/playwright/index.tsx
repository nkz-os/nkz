import { beforeMount } from '@playwright/experimental-ct-react/hooks';
import '../../design-tokens/dist/tokens.css';

// El perfil se pasa por hooksConfig y se aplica como en producción: atributo
// data-theme en el elemento raíz. Sin esto, todo se renderiza con `page`.
beforeMount<{ profile?: string }>(async ({ hooksConfig }) => {
  const profile = hooksConfig?.profile ?? 'page';
  document.documentElement.setAttribute('data-theme', profile);
  document.body.style.margin = '0';
  document.body.style.padding = '16px';

  // tokens.css pulls Inter from Google Fonts with display=swap: first paint uses
  // the fallback stack and swaps when the network lands. Without this wait every
  // screenshot races that swap. document.fonts.ready resolves once no font load
  // is pending — including the failure case, so this cannot hang offline.
  await document.fonts.ready;
});
