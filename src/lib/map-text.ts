/** Leaflet treats string content as HTML, including names loaded from saves. */
export function tooltipText(value: string): HTMLElement {
  const element = document.createElement('span');
  element.textContent = value;
  return element;
}
