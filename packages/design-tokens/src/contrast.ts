/**
 * Contraste WCAG 2.1 sobre colores opacos.
 *
 * Solo acepta hex de 6 dígitos. Los valores `transparent` y `rgba(...)` que
 * existen en los perfiles viewer/viewer-light NO son medibles contra un fondo
 * desconocido, así que se rechazan en vez de devolver un número inventado.
 */
export function relativeLuminance(hex: string): number {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) throw new Error(`relativeLuminance: se esperaba hex de 6 dígitos, se recibió "${hex}"`);
  const v = m[1]!;
  const channels = [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16) / 255);
  const linear = channels.map((ch) => (ch <= 0.03928 ? ch / 12.92 : ((ch + 0.055) / 1.055) ** 2.4));
  return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
}

export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}
