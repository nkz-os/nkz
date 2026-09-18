import { beforeMount } from '@playwright/experimental-ct-react/hooks';
import '../../design-tokens/dist/tokens.css';

// El perfil se pasa por hooksConfig y se aplica como en producción: atributo
// data-theme en el elemento raíz. Sin esto, todo se renderiza con `page`.
beforeMount<{ profile?: string }>(async ({ hooksConfig }) => {
  const profile = hooksConfig?.profile ?? 'page';
  document.documentElement.setAttribute('data-theme', profile);
  document.body.style.margin = '0';
  document.body.style.padding = '16px';
});
